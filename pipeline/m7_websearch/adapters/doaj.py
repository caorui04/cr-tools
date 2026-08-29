"""DOAJ 适配器（T7 中文归并 / T6 国际扇出复用）。

搜索 API 免费无需 key（2 req/s，单查询最多前 1000 条），元数据 CC0。
中文通道 = 英译关键词 + bibjson.journal.language:ZH 过滤（期刊语言随文章
记录返回；文章级 bibjson.language 实测大面积缺失不可用，2026-08-12 修正）。
侦察：中文 OA 期刊 319 种，中文唯一合规一键下载通道——DOAJ 收录本身即
OA，link fulltext = OA 直链。出站走 U4 http_client.get_json。
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from pipeline_core.http_client import get_json

from ..schema import WebResult

logger = logging.getLogger(__name__)

_BASE = "https://doaj.org/api/v4"
_TIMEOUT = 20


class DoajAdapter:
    id = "doaj"
    label = "DOAJ"

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
        # 中文归并：Lucene 语言过滤。注意过滤路径是 bibjson.journal.language
        # （期刊语言随文章记录返回），不是 bibjson.language（文章级语言字段
        # 实测大面积缺失，过滤返回 0——2026-08-12 实测修正，第十七条原口径
        # 的 journal 级实现）
        lucene = f'({q}) AND bibjson.journal.language:{lang.upper()}' if lang else q
        url = f"{_BASE}/search/articles/{quote(lucene, safe='')}"

        res = get_json(url, timeout=_TIMEOUT, params={"pageSize": min(100, max_results)})
        if not res.ok or not isinstance(res.data, dict):
            logger.warning(f"[websearch] DOAJ 请求失败: {res.error or '响应非 JSON'}")
            return []

        results: list[WebResult] = []
        for item in (res.data.get("results") or [])[:max_results]:
            bib = item.get("bibjson") or {}
            title = (bib.get("title") or "").strip()
            if not title:
                continue
            authors = [a.get("name", "") for a in (bib.get("author") or [])][:10]
            doi = next(
                (i.get("id") for i in (bib.get("identifier") or []) if i.get("type") == "doi"),
                None,
            )
            # DOAJ 收录即 OA：fulltext 链接作 OA 直链；无则退详情页
            fulltext = next(
                (l.get("url") for l in (bib.get("link") or []) if l.get("type") == "fulltext"),
                None,
            )
            article_url = f"https://doaj.org/article/{item['id']}" if item.get("id") else (fulltext or "")
            results.append(
                WebResult(
                    title=title,
                    source=self.label,
                    authors=[a for a in authors if a],
                    journal=(bib.get("journal") or {}).get("title") or None,
                    year=str(bib["year"]) if bib.get("year") else None,
                    abstract=(bib.get("abstract") or "").strip()[:500] or None,
                    doi=doi,
                    url=article_url,
                    oa_url=fulltext,
                )
            )
        return results
