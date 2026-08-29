"""M6 cite skill — firstpage_to_biblio 独立模块。"""

from __future__ import annotations

from dataclasses import dataclass

# 模块级 provider 注入机制
_provider = None  # LLMProvider | None


def set_provider(p):
    """注入 LLMProvider 实例。"""
    global _provider
    _provider = p


@dataclass
class BiblioDraft:
    biblio: dict
    field_confidence: dict
    formatted: str


def firstpage_to_biblio(
    firstpage_text: str,
    firstpage_image_path: str | None,
    format: str,
) -> BiblioDraft:
    """从首页文本提取文献著录信息。provider 通过 set_provider() 注入。"""
    if _provider is None:
        return BiblioDraft(
            biblio={k: None for k in [
                "authors", "title", "type", "venue", "year",
                "volume", "issue", "pages", "doi", "publisher", "url",
            ]},
            field_confidence={k: 0.0 for k in [
                "authors", "title", "type", "venue", "year",
                "volume", "issue", "pages", "doi",
            ]},
            formatted=f"[stub] {_extract_title_heuristic(firstpage_text)}",
        )

    raw = _provider.extract_biblio(firstpage_text, format)
    biblio = _postprocess(raw.get("biblio", {}))
    conf = raw.get("field_confidence", {})
    formatted = render_formatted(biblio, format)
    return BiblioDraft(biblio=biblio, field_confidence=conf, formatted=formatted)


def _extract_title_heuristic(text: str) -> str:
    """启发式提取标题。"""
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 5:
            return line[:80]
    return text[:80] if text else ""


def _postprocess(biblio: dict) -> dict:
    """规则后处理：type 枚举校验、页码规范化、空字符串→null。"""
    import re
    valid_types = {"J", "M", "C", "D", "R", "EB/OL"}
    result = dict(biblio)
    if result.get("type") not in valid_types:
        result["type"] = None
    # 页码规范化：去除 "pp." "p." "pages" 前缀
    pages = result.get("pages")
    if pages and isinstance(pages, str):
        pages = re.sub(r'^(pp\.|p\.|pages)\s*', '', pages, flags=re.IGNORECASE).strip()
        result["pages"] = pages
    for k, v in result.items():
        if v == "":
            result[k] = None
    return result


def render_formatted(biblio: dict, format: str) -> str:
    """按指定格式渲染引用字符串。"""
    authors = biblio.get("authors") or []
    author_str = ", ".join(authors) if authors else ""
    title = biblio.get("title") or ""
    typ = biblio.get("type") or ""
    venue = biblio.get("venue") or ""
    year = biblio.get("year") or ""
    volume = biblio.get("volume") or ""
    issue = biblio.get("issue") or ""
    pages = biblio.get("pages") or ""

    if format == "GB/T 7714-2015":
        parts = []
        if author_str:
            parts.append(author_str)
        if title:
            parts.append(f"{title}[{typ}]" if typ else title)
        if venue:
            parts.append(venue)
        if year:
            vol_iss = ""
            if volume:
                vol_iss = str(volume)
            if issue:
                vol_iss += f"({issue})"
            parts.append(f"{year}, {vol_iss}: {pages}".strip(": "))
        return ". ".join(parts) + "."
    elif format == "APA":
        parts = []
        if author_str:
            parts.append(f"{author_str} ({year})" if year else author_str)
        if title:
            parts.append(f"{title}.")
        if venue:
            vol = f", {volume}" if volume else ""
            iss = f"({issue})" if issue else ""
            pg = f", {pages}" if pages else ""
            parts.append(f"{venue}{vol}{iss}{pg}.")
        return " ".join(parts)
    elif format == "MLA":
        # MLA 9th: Author. "Title." Journal, vol. Volume, no. Issue, Year, pp. Pages.
        parts = []
        if author_str:
            parts.append(f'{author_str}.')
        if title:
            parts.append(f'"{title}."')
        if venue:
            parts.append(f'{venue},')
        if volume:
            parts.append(f'vol. {volume},')
        if issue:
            parts.append(f'no. {issue},')
        if year:
            parts.append(f'{year},')
        if pages:
            parts.append(f'pp. {pages}.')
        return " ".join(parts).rstrip(",") + ("." if not parts[-1].endswith(".") else "")
    elif format == "Chicago":
        # Chicago: Author. "Title." Journal Volume, no. Issue (Year): Pages.
        parts = []
        if author_str:
            parts.append(f'{author_str}.')
        if title:
            parts.append(f'"{title}."')
        venue_vol = venue or ""
        if volume:
            venue_vol += f" {volume}"
        if venue_vol.strip():
            parts.append(f'{venue_vol.strip()},')
        if issue:
            parts.append(f'no. {issue}')
        if year:
            parts.append(f'({year})')
        if pages:
            parts[-1] = parts[-1] + f': {pages}.'
        result = " ".join(parts)
        return result.rstrip(",") + ("." if not result.endswith(".") else "")
    else:
        # fallback
        return f"{author_str}. {title}. {venue}, {year}."
