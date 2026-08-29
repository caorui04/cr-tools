"""页计划 JSON 生成与读写。

产出 plans/<doc_id>_page_plan.json（C2 §1 Schema）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline_core.atomic import atomic_write


@dataclass
class PagePlan:
    doc_id: str
    source_file: str
    total_pages: int
    classification: dict
    translate_flag: bool
    created_at: str
    pages: list[dict]


def build_page_plan(
    doc_id: str,
    source_file: str,
    total_pages: int,
    classification,
    pages: list,
    translate_flag: bool,
) -> PagePlan:
    """组装页计划。"""
    return PagePlan(
        doc_id=doc_id,
        source_file=source_file,
        total_pages=total_pages,
        classification={
            "composition": classification.composition,
            "language": classification.language,
        },
        translate_flag=translate_flag,
        created_at=datetime.now(timezone.utc).isoformat(),
        pages=[{"page": p.page, "action": p.action, "images": p.images} for p in pages],
    )


def write_page_plan(plans_dir: Path, plan: PagePlan) -> None:
    """写入 plans/<doc_id>_page_plan.json（原子写）。"""
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / f"{plan.doc_id}_page_plan.json"
    content = json.dumps(asdict(plan), ensure_ascii=False, indent=2)
    atomic_write(path, content)
