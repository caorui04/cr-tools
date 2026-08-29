"""管线环节进度上报 — 状态页进度条数据源。

各环节（M1 识别 / M2 OCR抽取 / M3 翻译 / M4 向量化）在处理过程中
调用 report() 覆盖式写入 <pipeline_root>/progress/<stage>.json：

    {"stage": "m2", "doc_id": "a3f8c2d4e5f6", "done": 12, "total": 120,
     "updated_at": "2026-07-30T13:00:00+00:00"}

与 M2/M3 断点续传的 <doc_id>.progress 文件共存于 progress/ 目录，
通过后缀（.json vs .progress）区分，互不干扰。

写入轻量（单次覆盖写，不 fsync）；/status 通过 read_all() 读取全部环节。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STAGES: tuple[str, ...] = ("m1", "m2", "m3", "m4")


def report(
    stage: str,
    doc_id: str,
    done: int,
    total: int,
    root: Path | str | None = None,
) -> None:
    """上报某环节当前进度（覆盖写 progress/<stage>.json）。

    root 缺省时按 config.yaml 的 pipeline_root 解析。
    调用方（各模块埋点）必须自行 try/except——进度写入失败不得影响主流程。
    """
    if stage not in STAGES:
        raise ValueError(f"非法 stage: {stage}（应为 {STAGES} 之一）")

    if root is None:
        from .config import load_config

        root = load_config().pipeline_root
    root = Path(root)

    progress_dir = root / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "stage": stage,
        "doc_id": str(doc_id),
        "done": int(done),
        "total": int(total),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (progress_dir / f"{stage}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def read_all(root: Path | str) -> dict[str, dict | None]:
    """读取全部环节进度：{stage: {"doc_id","done","total","updated_at"} | None}。

    文件缺失或损坏（如写入中途被读）时该环节为 None。
    """
    root = Path(root)
    progress_dir = root / "progress"
    result: dict[str, dict | None] = {}
    for stage in STAGES:
        entry = None
        path = progress_dir / f"{stage}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = {
                "doc_id": data.get("doc_id"),
                "done": data.get("done"),
                "total": data.get("total"),
                "updated_at": data.get("updated_at"),
            }
        except Exception:
            entry = None
        result[stage] = entry
    return result
