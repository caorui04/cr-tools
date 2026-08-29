"""arXiv 适配器（T6：旧 /web/literature_search 独立入口迁入适配层，A/C 5）。

arXiv 开放 API（Atom XML，std 库 xml.etree 解析，无新依赖）。全部内容 OA：
url = 摘要页（rel=alternate），oa_url = PDF 直链（title=pdf）。doi 取自
arxiv:doi 元素（多数预印本无 DOI）。
定位：理工预印本库，人文社科命中稀疏（蓝图第十五条，界面须有定位说明）。
出站走 U4 http_client.get_json。
"""

from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET

from pipeline_core.http_client import get_json

from ..schema import WebResult

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivAdapter:
    id = "arxiv"
    label = "arXiv"

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
        url = (
            "https://export.arxiv.org/api/query?search_query=all:"
            f"{urllib.parse.quote(q)}&start=0&max_results={min(50, max_results)}"
        )
        res = get_json(url, timeout=_TIMEOUT)
        if not res.ok or not isinstance(res.data, str):
            logger.warning(f"[websearch] arXiv 请求失败: {res.error or '响应非文本'}")
            return []

        results: list[WebResult] = []
        try:
            root_el = ET.fromstring(res.data)
        except ET.ParseError as e:
            logger.warning(f"[websearch] arXiv 响应解析失败: {e}")
            return []
        for entry in root_el.findall("a:entry", _NS):
            title = (entry.findtext("a:title", default="", namespaces=_NS) or "").strip()
            if not title:
                continue
            summary = (entry.findtext("a:summary", default="", namespaces=_NS) or "").strip()
            authors = [
                (a.findtext("a:name", default="", namespaces=_NS) or "").strip()
                for a in entry.findall("a:author", _NS)
            ][:10]
            page_url = ""
            pdf_url = None
            for l in entry.findall("a:link", _NS):
                href = l.get("href", "")
                if l.get("title") == "pdf" and href:
                    pdf_url = href
                elif l.get("rel") == "alternate" and href:
                    page_url = href
            published = (entry.findtext("a:published", default="", namespaces=_NS) or "")[:10]
            doi_raw = (entry.findtext("arxiv:doi", default="", namespaces=_NS) or "").strip()
            results.append(
                WebResult(
                    title=" ".join(title.split()),  # Atom title 常带换行缩进
                    source=self.label,
                    authors=[a for a in authors if a],
                    journal=None,
                    year=published[:4] or None,
                    abstract=summary[:300] or None,
                    doi=doi_raw or None,
                    url=page_url or (pdf_url or ""),
                    oa_url=pdf_url,  # arXiv 全 OA
                )
            )
            if len(results) >= max_results:
                break
        return results
