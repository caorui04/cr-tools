"""MD 语义分块器。

按标题/段落切分，300–800 tokens，10% overlap。
解析 <!-- page N --> 标注记录页码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAGE_MARKER = re.compile(r"<!--\s*page\s*(\d+)\s*-->")


@dataclass
class Chunk:
    seq: int
    text: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: str = ""


def estimate_tokens(text: str) -> int:
    """启发式 token 估计：中文 ≈ 1.5 字符/token，英文 ≈ 4 字符/token。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def chunk_markdown(
    md_text: str,
    target_tokens: tuple[int, int] = (300, 800),
    overlap_ratio: float = 0.10,
) -> list[Chunk]:
    """按 MD 结构语义分块。

    Args:
        md_text: Markdown 文本
        target_tokens: (min, max) token 范围
        overlap_ratio: 相邻 chunk 重叠比例

    Returns:
        Chunk 列表，按文档顺序。
    """
    min_tok, max_tok = target_tokens
    lines = md_text.split("\n")

    # 1) 按空行和标题分段
    segments: list[dict] = []
    current_lines: list[str] = []
    current_page: int | None = None
    current_heading = ""

    for line in lines:
        # 检测页码标注
        m = _PAGE_MARKER.search(line)
        if m:
            if current_lines:
                segments.append({
                    "lines": current_lines,
                    "page_start": current_page,
                    "heading": current_heading,
                })
                current_lines = []
            current_page = int(m.group(1))
            continue

        # 检测标题
        if line.startswith("#"):
            if current_lines:
                segments.append({
                    "lines": current_lines,
                    "page_start": current_page,
                    "heading": current_heading,
                })
                current_lines = []
            current_heading = line.lstrip("#").strip()
            current_lines.append(line)
            continue

        current_lines.append(line)

    if current_lines:
        segments.append({
            "lines": current_lines,
            "page_start": current_page,
            "heading": current_heading,
        })

    # 2) 合并短段落、拆分长段落 → chunk
    chunks: list[Chunk] = []
    buffer_lines: list[str] = []
    buffer_page_start: int | None = None
    buffer_heading = ""
    seq = 0
    overlap_chars = 0

    for seg in segments:
        seg_text = "\n".join(seg["lines"])
        seg_tokens = estimate_tokens(seg_text)

        if estimate_tokens("\n".join(buffer_lines)) + seg_tokens < max_tok:
            if buffer_page_start is None:
                buffer_page_start = seg["page_start"]
            if seg["heading"]:
                buffer_heading = seg["heading"]
            buffer_lines.extend(seg["lines"])
        else:
            # 输出当前 buffer
            if buffer_lines:
                chunk_text = "\n".join(buffer_lines)
                chunks.append(Chunk(
                    seq=seq, text=chunk_text,
                    page_start=buffer_page_start,
                    heading_path=buffer_heading,
                ))
                seq += 1
                # overlap: carry trailing text into next buffer
                buffer_lines = []
                if overlap_ratio > 0:
                    overlap_chars = max(1, int(len(chunk_text) * overlap_ratio))
                    overlap_text = chunk_text[-overlap_chars:]
                    buffer_lines = [overlap_text] if overlap_text else []
            else:
                buffer_lines = seg["lines"]
            buffer_page_start = seg["page_start"]
            if seg["heading"]:
                buffer_heading = seg["heading"]

    if buffer_lines:
        chunks.append(Chunk(
            seq=seq, text="\n".join(buffer_lines),
            page_start=buffer_page_start,
            heading_path=buffer_heading,
        ))

    # 3) 填充 page_end
    for i, c in enumerate(chunks):
        if i + 1 < len(chunks):
            c.page_end = chunks[i + 1].page_start
        else:
            c.page_end = c.page_start

    return chunks
