"""路径穿越校验具名验证器（U3 文件访问网关契约 3.1，批次 F）。

从 server/routes.py 迁移三处既有校验，行为零变化：
同样的正则、同样的拒绝文案；合法返回原值，非法抛 PathUnsafeError，
由 routes 层捕获转 HTTPException(4xx)。本模块不依赖 fastapi。
"""

from __future__ import annotations

import re
from pathlib import Path

# paper_id 白名单：仅字母数字下划线连字符
PAPER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 草稿名放宽支持中文（用户 docx 文件名常见中文），仍防路径穿越（无 / \ 与 ..）
DRAFT_NAME_RE = re.compile(r"^(?!.*\.\.)[\w一-鿿.\- ]+\.md$")  # 等价 routes 原 [\w\u4e00-\u9fff.\- ]


class PathUnsafeError(Exception):
    """路径组件未通过穿越校验（routes 层捕获转 4xx）。"""


def safe_paper_id(paper_id: str) -> str:
    """校验 paper_id 防路径穿越（仅字母数字下划线连字符）。"""
    pid = (paper_id or "").strip()
    if not pid or not PAPER_ID_RE.match(pid):
        raise PathUnsafeError(f"非法的论文项目 ID: {paper_id!r}")
    return pid


def safe_draft_name(name: str) -> str:
    """校验草稿逻辑名（中文兼容 + 禁 ..），合法返回原值。"""
    if not DRAFT_NAME_RE.match(name):
        raise PathUnsafeError(f"非法的草稿文件名: {name!r}（仅 *.md）")
    return name


def safe_filename(name: str) -> str:
    """校验 inputs 文件名为单组件（无目录分隔），合法返回原值。"""
    if Path(name).name != name:
        raise PathUnsafeError("非法文件名")
    return name
