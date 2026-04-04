import requests
import xml.etree.ElementTree as ET
from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from loguru import logger
from typing import Any
from time import sleep


ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


@register_retriever("pubmed")
class PubmedRetriever(BaseRetriever):
    """
    PubMed retriever，使用 NCBI E-utilities。
    config.source.pubmed.query: PubMed 搜尋字串（MeSH terms、journal names 皆可）
    config.source.pubmed.api_key: NCBI API key（選填，有 key 可提高 rate limit）
    config.source.pubmed.reldate: 抓取最近幾天的論文（預設 2）
    """

    def _build_params(self, extra: dict) -> dict:
        params = {}
        if api_key := self.retriever_config.get("api_key"):
            params["api_key"] = api_key
        params.update(extra)
        return params

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        query = self.retriever_config.get("query")
        if not query:
            raise ValueError("pubmed.query must be specified, e.g. 'cognitive neuroscience'")
        reldate = self.retriever_config.get("reldate", 2)

        # Step 1: esearch — 取得 PubMed ID 清單
        params = self._build_params({
            "db": "pubmed",
            "term": query,
            "reldate": reldate,
            "datetype": "edat",
            "retmax": 100,
            "retmode": "json",
        })
        resp = requests.get(ESEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        if self.config.executor.debug:
            ids = ids[:5]
        logger.info(f"[pubmed] Found {len(ids)} paper IDs")

        # Step 2: efetch — 批次取得摘要 XML
        raw_papers = []
        for i in range(0, len(ids), 20):
            batch = ids[i:i+20]
            params = self._build_params({
                "db": "pubmed",
                "id": ",".join(batch),
                "rettype": "abstract",
                "retmode": "xml",
            })
            resp = requests.get(EFETCH_URL, params=params, timeout=60)
            resp.raise_for_status()
            articles = self._parse_xml(resp.text)
            raw_papers.extend(articles)
            sleep(0.4)  # 無 API key 時 rate limit 為 3 req/s

        return raw_papers

    def _parse_xml(self, xml_text: str) -> list[dict]:
        root = ET.fromstring(xml_text)
        papers = []
        for article in root.findall(".//PubmedArticle"):
            try:
                medline = article.find("MedlineCitation")
                art = medline.find("Article")
                title = art.findtext("ArticleTitle", "").strip()
                # 摘要（有些文章有多段）
                abstract_parts = art.findall(".//AbstractText")
                abstract = " ".join(
                    (p.get("Label", "") + ": " if p.get("Label") else "") + (p.text or "")
                    for p in abstract_parts
                ).strip()
                # 作者
                authors = []
                for author in art.findall(".//Author"):
                    last = author.findtext("LastName", "")
                    fore = author.findtext("ForeName", "")
                    if last:
                        authors.append(f"{fore} {last}".strip())
                # PMID 與 DOI
                pmid = medline.findtext("PMID", "")
                doi = ""
                for id_node in article.findall(".//ArticleId"):
                    if id_node.get("IdType") == "doi":
                        doi = id_node.text or ""
                papers.append({
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "pmid": pmid,
                    "doi": doi,
                })
            except Exception as e:
                logger.warning(f"[pubmed] Failed to parse article: {e}")
        return papers

    def convert_to_paper(self, raw: dict[str, Any]) -> Paper | None:
        title = raw.get("title", "").strip()
        abstract = raw.get("abstract", "").strip()
        if not title or not abstract:
            return None
        pmid = raw.get("pmid", "")
        doi = raw.get("doi", "")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
        pdf_url = f"https://doi.org/{doi}" if doi else None
        return Paper(
            source="pubmed",
            title=title,
            authors=raw.get("authors", []),
            abstract=abstract,
            url=url,
            pdf_url=pdf_url,
        )
