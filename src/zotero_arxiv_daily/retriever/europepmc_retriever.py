import requests
from datetime import datetime, timedelta
from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from loguru import logger
from typing import Any


EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


@register_retriever("europepmc")
class EuropepmcRetriever(BaseRetriever):
    """
    Europe PMC retriever，涵蓋 PubMed Central、生醫期刊與開放取用文章。
    config.source.europepmc.query: Europe PMC 搜尋字串
    config.source.europepmc.reldate: 抓取最近幾天的論文（預設 2）
    """

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        query = self.retriever_config.get("query")
        if not query:
            raise ValueError("europepmc.query must be specified, e.g. 'cognitive neuroscience'")
        reldate = self.retriever_config.get("reldate", 2)
        since = (datetime.now() - timedelta(days=reldate)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        date_filter = f"FIRST_PDATE:[{since} TO {today}]"
        full_query = f"({query}) AND {date_filter} AND OPEN_ACCESS:y"

        all_papers = []
        cursor = "*"
        for _ in range(5):  # 最多 5 頁（每頁 100 筆）
            params = {
                "query": full_query,
                "format": "json",
                "pageSize": 100,
                "cursorMark": cursor,
                "sort": "FIRST_PDATE desc",
                "resultType": "core",
            }
            try:
                resp = requests.get(EUROPEPMC_URL, params=params, timeout=30)
                resp.raise_for_status()
            except Exception as e:
                logger.warning(f"[europepmc] Request failed: {e}")
                break
            data = resp.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                break
            all_papers.extend(results)
            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

        if self.config.executor.debug:
            all_papers = all_papers[:5]
        logger.info(f"[europepmc] Fetched {len(all_papers)} papers")
        return all_papers

    def convert_to_paper(self, raw: dict[str, Any]) -> Paper | None:
        title = raw.get("title", "").strip().rstrip(".")
        abstract = raw.get("abstractText", "").strip()
        if not title or not abstract:
            return None
        pmid = raw.get("pmid", "")
        pmcid = raw.get("pmcid", "")
        doi = raw.get("doi", "")
        # 優先用 PMC 連結（有全文），次用 DOI，再用 PMID
        if pmcid:
            url = f"https://europepmc.org/article/pmc/{pmcid}"
            pdf_url = f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"
        elif pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            pdf_url = f"https://doi.org/{doi}" if doi else None
        elif doi:
            url = f"https://doi.org/{doi}"
            pdf_url = url
        else:
            return None
        # 作者
        authors = []
        for a in raw.get("authorList", {}).get("author", []):
            full = a.get("fullName") or f"{a.get('firstName','')} {a.get('lastName','')}".strip()
            if full:
                authors.append(full)
        return Paper(
            source="europepmc",
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            pdf_url=pdf_url,
        )
