"""队列分流 — 按分类结果将文档移入对应队列。

- 含 ocr 页 → 01_OCR队列/
- [T] 标记 → 02_翻译队列/
- 两者皆满足 → 先 01_OCR队列/（OCR 必须先于翻译）
"""

from __future__ import annotations

from pathlib import Path

from pipeline_core.atomic import atomic_move


def route_document(root: Path, source_file: Path, page_plan, translate_flag: bool) -> str:
    """判断文档路由目标。返回目标队列目录的相对名。

    组装方裁定（修订 2026-07-31）：**PDF 一律 → 01_OCR队列**。
    理由：M3/M4 只消费 .md（M3 dispatcher 仅处理 *.ocr.md / *.md），
    纯文本 [T] PDF 若直送 02_翻译队列 会卡死（无任何模块消费 PDF）。
    M2 统一负责 extract/ocr 产出 .ocr.md，再按 translate_flag 分流
    （有 [T] → 02_翻译队列；无 → 03_知识库原文）。
    """
    return "01_OCR队列"


def move_to_queue(root: Path, source_file: Path, target_queue: str) -> Path:
    """原子移动文件到目标队列目录。"""
    dst_dir = root / target_queue
    return atomic_move(source_file, dst_dir)
