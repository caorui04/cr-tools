"""目录初始化。

启动时按 C1 §2 创建全部管线目录。
"""

from __future__ import annotations

import os
from pathlib import Path

# C1 §2 定义的全部目录（相对于 pipeline_root）
_DIRECTORIES: list[str] = [
    "00_待处理",
    "01_OCR队列",
    "02_翻译队列",
    "03_知识库原文",
    "04_已入库归档",
    "99_异常",
    "plans",
    "progress",
    "sidecar",
    "assets",
    "sync_outbox",
    "logs",
    "vector_db",
]


def init_pipeline_dirs(root: Path) -> None:
    """按 C1 §2 创建全部目录（含子目录），已存在则跳过。

    创建后校验所有路径可写。
    """
    root.mkdir(parents=True, exist_ok=True)
    for d in _DIRECTORIES:
        dir_path = root / d
        dir_path.mkdir(parents=True, exist_ok=True)

    # 确保 vector_db 目录存在（C4 §1）
    (root / "vector_db").mkdir(parents=True, exist_ok=True)

    # 校验可写
    for d in _DIRECTORIES:
        dir_path = root / d
        if not os.access(dir_path, os.W_OK):
            raise PermissionError(f"目录不可写: {dir_path}")
