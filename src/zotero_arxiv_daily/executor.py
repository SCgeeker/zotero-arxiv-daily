from loguru import logger
from pyzotero import zotero
from pyzotero import errors as zotero_errors
from omegaconf import DictConfig, ListConfig
from .utils import glob_match
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
import random
import re
import time
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from openai import OpenAI
from tqdm import tqdm


# Zotero API 偶發的暫時性狀態碼。pyzotero 的 ERROR_CODES 未涵蓋任何 5xx，
# 這些都會落到通用的 errors.HTTPError，且 pyzotero 本身不會重試。
_TRANSIENT_STATUS = frozenset({500, 502, 503, 504})
_STATUS_IN_MESSAGE = re.compile(r"Code:\s*(\d{3})")


def _extract_status_code(exc: Exception) -> int | None:
    """從 pyzotero 例外取出 HTTP 狀態碼，取不到則回傳 None。"""
    # error_handler 是以 `raise ... from exc` 拋出，__cause__ 會是 httpx 例外。
    response = getattr(getattr(exc, "__cause__", None), "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    if match := _STATUS_IN_MESSAGE.search(str(exc)):
        return int(match.group(1))
    return None


def retry_on_transient_error(
    fn,
    *,
    max_attempts: int = 5,
    base_delay: float = 2.0,
    sleep=None,
    desc: str = "Zotero API",
):
    """對 Zotero 暫時性錯誤做指數退避重試。

    只重試 5xx 與連線失敗；4xx（設定錯誤、認證失敗）與非 pyzotero 例外
    一律立刻拋出，避免把真正的錯誤拖成五倍時間。重試用盡後原樣拋出最後
    一個例外，讓 workflow 明確失敗而不是靜默回傳空結果。
    """
    sleep = sleep or time.sleep
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except zotero_errors.PyZoteroError as e:
            retryable = isinstance(e, zotero_errors.CouldNotReachURLError) or (
                _extract_status_code(e) in _TRANSIENT_STATUS
            )
            if not retryable or attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                f"{desc} transient failure ({type(e).__name__}), "
                f"attempt {attempt}/{max_attempts}, retrying in {delay:.0f}s"
            )
            sleep(delay)


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(
            api_key=config.llm.api.key,
            base_url=config.llm.api.base_url,
            timeout=float(config.llm.get('timeout', 120)),
        )
    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = retry_on_transient_error(
            lambda: zot.everything(zot.collections()), desc="Zotero collections"
        )
        collections = {c['key']:c for c in collections}
        # 文獻庫項目數已超過 4500 筆，everything() 需分頁數十次；
        # 深層分頁偶爾會撞到 502，整段重試（分頁查詢本身是冪等的）。
        corpus = retry_on_transient_error(
            lambda: zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint')),
            desc="Zotero items",
        )
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        failed_sources = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            try:
                papers = retriever.retrieve_papers()
            except Exception as e:
                # 單一來源失敗(如 arxiv HTTP 429)不應拖垮整個 pipeline；
                # 記錄錯誤後繼續跑其他來源。
                logger.error(f"Failed to retrieve {source} papers: {type(e).__name__}: {e}")
                failed_sources.append(source)
                continue
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        if failed_sources:
            logger.warning(f"Sources failed: {failed_sources}. Proceeding with remaining results.")
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
            min_relevance = self.config.executor.get('min_relevance', 0.0)
            if reranked_papers:
                scores = [p.score for p in reranked_papers if p.score is not None]
                logger.info(f"Score range: min={min(scores):.3f}, max={max(scores):.3f}, mean={sum(scores)/len(scores):.3f}")
            if min_relevance > 0:
                before = len(reranked_papers)
                reranked_papers = [p for p in reranked_papers if p.score is not None and p.score >= min_relevance]
                logger.info(f"Relevance filter (>={min_relevance}): {before} → {len(reranked_papers)} papers")
            logger.info("Generating TLDR and affiliations...")
            for p in tqdm(reranked_papers):
                p.generate_tldr(self.openai_client, self.config.llm)
                p.generate_affiliations(self.openai_client, self.config.llm)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
        logger.info("Sending email...")
        email_content = render_email(reranked_papers)
        send_email(self.config, email_content)
        logger.info("Email sent successfully")
