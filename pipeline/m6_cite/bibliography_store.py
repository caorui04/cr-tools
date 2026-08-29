"""M6 CSL 清单 store（D8，批次 P；依 U1 workbench_state 权威路径）。

routes.py 清单端点（add/remove/format/verify + insert_mark 查号）的纯逻辑抽离：
- 加载/保存：清单文件路径一律取 workbench-state ``paths.biblio``（U1 固化权威
  路径，禁止临时拼接）；写回时刷新 ``updated_at``；
- add：seq 顺序分配 + 选区/锚点可选元数据截断登记（doc_id 去重判断在调用方，
  经 ``find_by_doc_id``）；
- remove：按 seq 移除 + 全量重排（编号=顺序，正向生成）；
- set_format：格式切换全量重渲染（渲染层在 ``cite_skill.render_formatted``）。

对外 API 行为零变化：HTTP 参数解析/错误响应留在 routes 端点（薄壳）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from server import workbench_state as ws

from .cite_skill import render_formatted

DEFAULT_FORMAT = "GB/T 7714-2015"
FORMATS = ("GB/T 7714-2015", "APA", "MLA", "Chicago")


def biblio_path(paper_dir: Path) -> Path:
    """清单文件权威路径（U1）：workbench-state paths.biblio（ensure 幂等固化 + 自愈）。"""
    st = ws.ensure(paper_dir)
    return Path(st["paths"]["biblio"])


def load(paper_dir: Path) -> dict:
    """读清单；缺失/损坏返回空骨架（paper_id 取项目目录名）。"""
    p = biblio_path(paper_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
        except Exception:
            pass
    return {"paper_id": paper_dir.name, "format": DEFAULT_FORMAT, "items": []}


def save(paper_dir: Path, data: dict) -> None:
    """写清单（刷新 updated_at 后落盘）。"""
    p = biblio_path(paper_dir)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_by_doc_id(data: dict, doc_id: str) -> dict | None:
    """按 doc_id 查条目（去重判断 / insert_mark 查号用）。"""
    return next((it for it in data["items"] if it.get("doc_id") == doc_id), None)


def find_by_seq(data: dict, seq) -> dict | None:
    """按清单序号查条目。"""
    return next((it for it in data["items"] if it.get("seq") == seq), None)


def add_item(
    data: dict,
    doc_id: str,
    draft: dict,
    *,
    page=None,
    snippet=None,
    draft_id=None,
    anchor=None,
) -> dict:
    """追加条目（seq = 末尾顺序号）。调用方须先经 find_by_doc_id 判重。

    draft: ``{draft, formatted, confirmed}``（引用草稿生成结果）；
    page/snippet 为选区引用可选元数据（页码锚点/原文片段，截断防爆）；
    draft_id/anchor 为论文内锚点（M5 草稿联动：引用标记插入的草稿与段落）。
    """
    item = {
        "seq": len(data["items"]) + 1,
        "doc_id": doc_id,
        "bibliography": draft["draft"],
        "formatted": draft["formatted"],
        "confirmed": draft["confirmed"],
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if isinstance(page, int) and page > 0:
        item["page"] = page
    if isinstance(snippet, str) and snippet.strip():
        item["snippet"] = snippet.strip()[:500]
    if isinstance(draft_id, str) and draft_id.strip():
        item["draft_id"] = draft_id.strip()[:120]
    if isinstance(anchor, str) and anchor.strip():
        item["anchor"] = anchor.strip()[:200]
    data["items"].append(item)
    return item


def remove_item(data: dict, seq) -> bool:
    """按 seq 移除并重排编号（编号=顺序）；未找到返回 False。"""
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.get("seq") != seq]
    if len(data["items"]) == before:
        return False
    for idx, item in enumerate(data["items"], start=1):
        item["seq"] = idx
    return True


def set_format(data: dict, fmt: str) -> None:
    """切换格式并全量重渲染（编号=顺序，正向生成）。fmt 合法性由调用方按 FORMATS 校验。"""
    data["format"] = fmt
    for item in data["items"]:
        if item.get("bibliography"):
            item["formatted"] = render_formatted(item["bibliography"], fmt)
