"""OpenAlex 适配器（T7 中文归并 / T6 国际扇出复用）。

蓝图第十七条：中文 search 不分词返回 0（实测，与 key 无关）——中文通道 =
英译关键词 + filter=language:zh 归并（核实清单 A1：rural revitalization →
中文文献 169 条验证通过）。国际通道 = 英译词直搜（lang=None）。

出站走 U4 http_client.get_json；Key/base_url 从 config.yaml web_search.openalex
加载。数据 CC0；摘要为 inverted index 需还原；OA 直链 best_oa_location.pdf_url。
"""

from __future__ import annotations

import logging

from pipeline_core.http_client import get_json

from ..config import web_search_config
from ..schema import WebResult

logger = logging.getLogger(__name__)

_TIMEOUT = 20


def _reconstruct_abstract(inv: dict | None) -> str | None:
    """abstract_inverted_index {词: [位置...]} → 按位置还原句子。"""
    if not inv:
        return None
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    if not pos:
        return None
    return " ".join(pos[i] for i in sorted(pos))


class OpenAlexAdapter:
    id = "openalex"
    label = "OpenAlex"

    def search(
        self,
        query: str,
        max_results: int,
        *,
        query_orig: str | None = None,
        lang: str | None = None,
    ) -> list[WebResult]:
        q = (query or "").strip()
        if not q:
            return []
        cfg = web_search_config().get("openalex") or {}
        api_key = cfg.get("api_key") or ""
        base_url = (cfg.get("base_url") or "https://api.openalex.org").rstrip("/")
        if not api_key:
            raise RuntimeError("web_search.openalex.api_key 未配置")

        params: dict = {
            "search": q,
            "per-page": min(50, max_results),
            "api_key": api_key,
        }
        if lang:
            params["filter"] = f"language:{lang}"  # 中文归并：language:zh（第十七条）

        res = get_json(f"{base_url}/works", timeout=_TIMEOUT, params=params)
        if not res.ok or not isinstance(res.data, dict):
            logger.warning(f"[websearch] OpenAlex 请求失败: {res.error or '响应非 JSON'}")
            return []

        results: list[WebResult] = []
        for w in (res.data.get("results") or [])[:max_results]:
            title = (w.get("display_name") or "").strip()
            if not title:
                continue
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in (w.get("authorships") or [])
            ][:10]
            primary = w.get("primary_location") or {}
            journal = ((primary.get("source") or {}).get("display_name")) or None
            doi_raw = w.get("doi") or ""
            doi = doi_raw.removeprefix("https://doi.org/") or None
            oa_url = ((w.get("best_oa_location") or {}).get("pdf_url")) or None
            url = primary.get("landing_page_url") or w.get("id") or ""
            year = w.get("publication_year")
            results.append(
                WebResult(
                    title=title,
                    source=self.label,
                    authors=[a for a in authors if a],
                    journal=journal,
                    year=str(year) if year else None,
                    abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
                    doi=doi,
                    url=url,
                    oa_url=oa_url,
                )
            )
        return results
