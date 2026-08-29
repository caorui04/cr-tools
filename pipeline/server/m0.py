"""M0 导入逻辑 — 文件校验/重命名/sidecar 初始化/移入队列。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from pipeline_core.atomic import atomic_move
from pipeline_core.doc_id import compute_doc_id
from pipeline_core.sidecar import init_sidecar, read_sidecar

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".png", ".docx", ".txt", ".md"}


def validate_file(path: Path) -> str | None:
    """校验文件是否可导入。返回 None=通过；返回字符串=拒绝原因。"""
    if not path.exists():
        return f"文件不存在: {path}"
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return f"不支持的文件格式: {path.suffix}"
    if path.suffix.lower() == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            if doc.is_encrypted:
                doc.close()
                return "加密PDF，请先解密"
            doc.close()
        except Exception:
            # E-1（2026-08-01）：损坏文件不拒绝——用户可强制上传处理，
            # 结果可能不完整/不准确（由 routes 打 warning，UI 显著提示）
            pass
    return None


def compute_safe_filename(original: str) -> str:
    """去 emoji/特殊字符（保留中文/字母/数字/空格/连字符）→ 追加时间戳。"""
    name = Path(original).stem
    # 保留中文（\w + \u4e00-\u9fff），emoji/特殊字符 → _
    name = re.sub(r"[^\w\s\u4e00-\u9fff-]", "_", name, flags=re.UNICODE)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "unnamed"
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    ext = Path(original).suffix
    return f"{name}_{ts}{ext}"


def should_add_translate_flag(
    path: str, translate_all: bool, translate_paths: list[str] | None
) -> bool:
    """判断是否加 [T] 前缀。translate_paths 优先于 translate_all。"""
    if translate_paths:
        return path in translate_paths
    return translate_all


def ingest_file(
    src: Path, root: Path, translate_flag: bool
) -> tuple[str, str]:
    """单个文件导入。返回 (queued_name, doc_id)。"""
    doc_id = compute_doc_id(src)

    # 重复检测
    sidecar_dir = root / "sidecar"
    sidecar_path = sidecar_dir / f"{doc_id}.json"
    if sidecar_path.exists():
        return ("", "duplicate")

    # 安全化文件名
    safe_name = compute_safe_filename(src.name)
    if translate_flag:
        safe_name = f"[T]{safe_name}"

    # 页数检测
    page_count = 0
    if src.suffix.lower() == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(src))
            page_count = len(doc)
            doc.close()
        except Exception:
            page_count = 0

    # 初始化 sidecar
    init_sidecar(sidecar_dir, doc_id, src.name, page_count, translate_flag)

    # 原子移入 00_待处理/
    dest = atomic_move(src, root / "00_待处理", new_name=safe_name)
    return (safe_name, doc_id)
