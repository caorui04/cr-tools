"""首页归档 — 渲染 300DPI PNG + 提取首页文本。

产出 sidecar/<doc_id>_firstpage.png + _firstpage.txt（C1 §6）。
"""

from __future__ import annotations

from pathlib import Path

import fitz


def render_firstpage(doc: fitz.Document, output_path: Path) -> Path:
    """渲染首页 300DPI PNG，返回文件路径。"""
    page = doc[0]
    pix = page.get_pixmap(dpi=300)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_path))
    return output_path


def extract_firstpage_text(doc: fitz.Document) -> str:
    """提取首页纯文本。返回空字符串时记录 warning 但不阻塞。"""
    page = doc[0]
    return page.get_text("text")
