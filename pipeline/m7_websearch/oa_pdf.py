"""OA 落地页 PDF 直链发现（T8 走查驱动增强，2026-08-13）。

背景：DOAJ/OpenAlex 的 oa_url 约半数指向出版商**落地页 HTML** 而非 PDF 直链
（DOI 跳转、中文期刊详情页），直接下载只能拿到 HTML。本模块从落地页 HTML
中启发式提取 PDF 候选链接（.pdf 后缀 / download+pdf 组合，中文刊常见
downloadArticleFile.do?attachType=PDF 形态），供 oa_ingest 逐个尝试。

保守原则：只提取链接不猜 URL 模式；候选按出现顺序去重，调用方限量尝试。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_PDF_SUFFIX_RE = re.compile(r"\.pdf(\?|#|$)", re.IGNORECASE)

# Google Scholar 标准 meta（出版商页普遍携带，rddl/ykcs 等中文刊实测有）：
# <meta name="citation_pdf_url" content="..."> —— 优先级最高的权威 PDF 直链
_META_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# 落地页常见 PDF 入口的 href 特征（download/exportPdf/attachType=PDF/fulltext 等）
_PDF_HINT_RE = re.compile(r"pdf", re.IGNORECASE)
_ACTION_HINT_RE = re.compile(r"download|fulltext|exportpdf|attachType|articlepdf", re.IGNORECASE)


def looks_like_html(raw: bytes) -> bool:
    """内容是否像 HTML 页面（前 512 字节含 doctype/html 标签）。"""
    head = raw[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or b"<html" in head


def extract_pdf_candidates(raw: bytes, base_url: str) -> list[str]:
    """从落地页 HTML 提取 PDF 候选链接（citation_pdf_url meta 优先，其次 href 启发式）。"""
    text = raw.decode("utf-8", errors="replace")
    out: list[str] = []
    # 1) Google Scholar 标准 meta（权威直链，优先）
    for m in _META_PDF_RE.finditer(text):
        full = urljoin(base_url, m.group(1).strip())
        if full.startswith(("http://", "https://")) and full not in out:
            out.append(full)
    # 2) href 启发式（.pdf 后缀 / download+pdf 组合）
    for m in _HREF_RE.finditer(text):
        href = m.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if _PDF_SUFFIX_RE.search(href) or (
            _PDF_HINT_RE.search(href) and _ACTION_HINT_RE.search(href)
        ):
            full = urljoin(base_url, href)
            if full.startswith(("http://", "https://")) and full not in out:
                out.append(full)
    return out
