"""manifest JSONL 读写。

每行一条记录，入库对账事实源。
Schema 见 C1 §7。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ManifestEntry:
    doc_hash: str
    chunk_ids: list[str]
    version: int
    ts: str
    op: str  # upsert | delete

    def __post_init__(self):
        if self.op not in ("upsert", "delete"):
            raise ValueError(f"非法 op: {self.op}，必须为 upsert 或 delete")


def append_manifest(manifest_path: Path, entry: ManifestEntry) -> None:
    """追加一行 JSONL 到 manifest.jsonl。

    自动填充 ts（如果为空）。
    """
    if not entry.ts:
        entry.ts = datetime.now(timezone.utc).isoformat()

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "doc_hash": entry.doc_hash,
                    "chunk_ids": entry.chunk_ids,
                    "version": entry.version,
                    "ts": entry.ts,
                    "op": entry.op,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def read_manifest(manifest_path: Path) -> list[ManifestEntry]:
    """读取全部 manifest 条目。"""
    if not manifest_path.exists():
        return []

    entries: list[ManifestEntry] = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            entries.append(
                ManifestEntry(
                    doc_hash=data["doc_hash"],
                    chunk_ids=data["chunk_ids"],
                    version=data["version"],
                    ts=data.get("ts", ""),
                    op=data["op"],
                )
            )
    return entries


def find_by_doc_hash(manifest_path: Path, doc_hash: str) -> ManifestEntry | None:
    """按 doc_hash 查找最新条目（version 最大），用于幂等入库判断。"""
    entries = read_manifest(manifest_path)
    matched = [e for e in entries if e.doc_hash == doc_hash]
    if not matched:
        return None
    return max(matched, key=lambda e: e.version)
