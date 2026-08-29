"""断点续传进度 — 通用 stage 命名空间版（D6 入库，修 C4）。

格式：`progress/<doc_id>.<stage>.progress`，每行一个已完成单元序号
（M2 OCR=页码 / M3 翻译=块 seq，含义由调用方自定，本模块不解释）。

stage 命名空间使同一 doc_id 的不同环节断点互不干扰——取代原
`progress/<doc_id>.progress` 单文件方案（M2/M3 共写一名，靠「M2 完成时
清除」时序避免互踩；M2 异常残留时 M3 会误读页码当块 seq，C4 隐患）。
命名空间化后时序耦合自然消解：各环节只清自己的断点文件。

兼容性（2026-08-09 核查）：上线前 progress/ 目录无任何旧格式
`<doc_id>.progress` 在跑断点文件，按「全新开始」处理，不读旧名文件。
若日后从备份恢复旧文件，手工改名 `<doc_id>.<stage>.progress` 即可续跑。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 既有环节命名空间
STAGE_OCR = "ocr"            # M2：单元 = 页码
STAGE_TRANSLATE = "translate"  # M3：单元 = 块 seq


def _progress_path(root: Path, doc_id: str, stage: str) -> Path:
    if not stage or "/" in stage or "\\" in stage:
        raise ValueError(f"非法 stage: {stage!r}")
    return root / "progress" / f"{doc_id}.{stage}.progress"


def load_progress(root: Path, doc_id: str, stage: str) -> set[int]:
    """读取该 stage 已完成单元序号集合（文件缺失/损坏返回空集）。"""
    progress_path = _progress_path(root, doc_id, stage)
    if not progress_path.exists():
        return set()
    try:
        units: set[int] = set()
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                units.add(int(line))
        return units
    except Exception as e:
        logger.warning(f"读取进度文件失败: {progress_path}: {e}")
        return set()


def save_progress(root: Path, doc_id: str, stage: str, unit: int) -> None:
    """追加一个已完成单元序号。"""
    progress_path = _progress_path(root, doc_id, stage)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with open(progress_path, "a", encoding="utf-8") as f:
        f.write(f"{unit}\n")


def clear_progress(root: Path, doc_id: str, stage: str) -> None:
    """该 stage 完成后清除自己的断点文件（不动其它 stage）。"""
    progress_path = _progress_path(root, doc_id, stage)
    try:
        progress_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"清除进度文件失败: {progress_path}: {e}")
