"""doc_id / doc_hash 计算。

定义：doc_id == doc_hash == xxhash64(文件内容)[:12]（12 位小写 hex）
用途：全链唯一 ID；重复入库拦截；幂等锚点。
"""

from __future__ import annotations

from pathlib import Path

import xxhash


def content_hash(file_path: Path) -> str:
    """内容哈希：xxhash64(文件内容) → 16 位小写 hex（S6 语义拆分）。

    语义边界：仅用于「文件内容 → 内容指纹」（重复入库拦截 / doc_id 锚点）。
    输入是文件字节流——**禁止**拿来哈希路径/文件名字符串；
    路径确定性命名走 ``server.workbench_state.path_hash``。

    流式读取，大文件友好。
    """
    hasher = xxhash.xxh64()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_doc_id(file_path: Path) -> str:
    """xxhash64 文件内容 → 12 位小写 hex（= content_hash 前 12 位）。"""
    return content_hash(file_path)[:12]
