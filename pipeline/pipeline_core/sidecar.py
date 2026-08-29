"""sidecar JSON 读写。

每文档一个 sidecar/<doc_id>.json，记录元数据、处理阶段、错误信息。
Schema 见 C1 §5。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic import atomic_write


class SidecarError(Exception):
    """sidecar 读写或校验错误。"""


@dataclass
class Sidecar:
    """C1 §5 sidecar schema。"""

    doc_id: str
    source_file: str
    ingested_at: str
    page_count: int
    classification: dict = field(default_factory=lambda: {"composition": "mixed", "language": "B"})
    translate_flag: bool = False
    engines: dict = field(default_factory=lambda: {
        "identifier": "",
        "ocr": "",
        "translator": "",
    })
    low_confidence: bool = False
    retry_count: int = 0
    metadata_status: str = "pending"  # pending | auto_filled | confirmed
    bibliography: dict | None = None
    stage_ts: dict = field(default_factory=lambda: {
        "m0": None, "m1": None, "m2": None, "m3": None, "m4": None,
    })
    error: str | None = None

    def __post_init__(self):
        if self.metadata_status not in ("pending", "auto_filled", "confirmed"):
            raise SidecarError(f"非法 metadata_status: {self.metadata_status}")


_VALID_METADATA_STATUS = frozenset({"pending", "auto_filled", "confirmed"})


def _to_json(obj: Any) -> Any:
    """将 Sidecar dataclass 转换为可 JSON 序列化的 dict。"""
    return asdict(obj)


def _from_json(data: dict) -> Sidecar:
    """从 dict 构造 Sidecar，缺失字段填默认值。"""
    defaults = {
        "classification": {"composition": "mixed", "language": "B"},
        "translate_flag": False,
        "engines": {"identifier": "", "ocr": "", "translator": ""},
        "low_confidence": False,
        "retry_count": 0,
        "metadata_status": "pending",
        "bibliography": None,
        "stage_ts": {"m0": None, "m1": None, "m2": None, "m3": None, "m4": None},
        "error": None,
    }
    merged = {**defaults, **data}
    return Sidecar(
        doc_id=merged["doc_id"],
        source_file=merged["source_file"],
        ingested_at=merged.get("ingested_at", ""),
        page_count=merged.get("page_count", 0),
        classification=merged["classification"],
        translate_flag=merged["translate_flag"],
        engines=merged["engines"],
        low_confidence=merged["low_confidence"],
        retry_count=merged["retry_count"],
        metadata_status=merged["metadata_status"],
        bibliography=merged["bibliography"],
        stage_ts=merged["stage_ts"],
        error=merged["error"],
    )


def read_sidecar(sidecar_dir: Path, doc_id: str) -> Sidecar:
    """读取 sidecar/<doc_id>.json 并校验 schema。"""
    path = sidecar_dir / f"{doc_id}.json"
    if not path.exists():
        raise SidecarError(f"sidecar 文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return _from_json(data)


def write_sidecar(sidecar_dir: Path, sidecar: Sidecar) -> None:
    """写入 sidecar JSON（原子写）。"""
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / f"{sidecar.doc_id}.json"
    content = json.dumps(_to_json(sidecar), ensure_ascii=False, indent=2)
    atomic_write(path, content)


def update_sidecar_field(sidecar_dir: Path, doc_id: str, **fields) -> None:
    """部分更新 sidecar 字段（读→改→原子写）。

    Args:
        sidecar_dir: sidecar/ 目录
        doc_id: 文档 ID
        **fields: 要更新的字段键值对
    """
    sidecar = read_sidecar(sidecar_dir, doc_id)
    for key, value in fields.items():
        if hasattr(sidecar, key):
            setattr(sidecar, key, value)
    write_sidecar(sidecar_dir, sidecar)


def init_sidecar(
    sidecar_dir: Path,
    doc_id: str,
    source_file: str,
    page_count: int,
    translate_flag: bool = False,
) -> Sidecar:
    """创建初始 sidecar（M0 调用），所有可选字段填默认值。"""
    now = datetime.now(timezone.utc).isoformat()
    sidecar = Sidecar(
        doc_id=doc_id,
        source_file=source_file,
        ingested_at=now,
        page_count=page_count,
        translate_flag=translate_flag,
        stage_ts={"m0": now, "m1": None, "m2": None, "m3": None, "m4": None},
    )
    write_sidecar(sidecar_dir, sidecar)
    return sidecar
