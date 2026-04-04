import requests
from datetime import datetime, timezone, timedelta
from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from loguru import logger
from typing import Any


@register_retriever("osf")
class OsfRetriever(BaseRetriever):
    """
    OSF preprint retriever。支援 psyarxiv、socarxiv 等 OSF 旗下 provider。
    config.source.osf.provider: list of provider IDs，如 ["psyarxiv","socarxiv"]
    """

    def _retrieve_raw_papers(self) -> list[dict[str, Any]]:
        providers = self.retriever_config.get("provider", [])
        if not providers:
            raise ValueError("osf.provider must be a non-empty list, e.g. ['psyarxiv','socarxiv']")
        since = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
        all_papers = []
        for provider in providers:
            url = "https://api.osf.io/v2/preprints/"
            params = {
                "filter[provider]": provider,
                "filter[date_created][gte]": since,
                "page[size]": 50,
                "sort": "-date_created",
            }
            for page in range(1, 4):  # 最多 3 頁，約 150 篇
                params["page"] = page
                try:
                    resp = requests.get(url, params=params, timeout=30)
                    resp.raise_for_status()
                except Exception as e:
                    logger.warning(f"[osf] Failed to fetch {provider} page {page}: {e}")
                    break
                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break
                all_papers.extend(items)
                if not data.get("links", {}).get("next"):
                    break
        if self.config.executor.debug:
            all_papers = all_papers[:5]
        logger.info(f"[osf] Fetched {len(all_papers)} raw preprints")
        return all_papers

    def convert_to_paper(self, raw: dict[str, Any]) -> Paper | None:
        attrs = raw.get("attributes", {})
        title = attrs.get("title", "").strip()
        abstract = attrs.get("description", "").strip()
        if not title or not abstract:
            return None
        osf_id = raw["id"]
        url = f"https://osf.io/{osf_id}"
        # 作者從 relationships 取得（以 contributor 名稱列表為主）
        authors = self._fetch_authors(raw)
        return Paper(
            source="osf",
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            pdf_url=None,  # OSF 不提供直接 PDF 連結
        )

    def _fetch_authors(self, raw: dict) -> list[str]:
        try:
            contrib_url = (
                raw.get("relationships", {})
                .get("contributors", {})
                .get("links", {})
                .get("related", {})
                .get("href", "")
            )
            if not contrib_url:
                return []
            resp = requests.get(contrib_url, timeout=10)
            resp.raise_for_status()
            contribs = resp.json().get("data", [])
            names = []
            for c in contribs:
                embeds = c.get("embeds", {}).get("users", {}).get("data", {})
                if embeds:
                    full = embeds.get("attributes", {}).get("full_name", "")
                    if full:
                        names.append(full)
            return names
        except Exception:
            return []
