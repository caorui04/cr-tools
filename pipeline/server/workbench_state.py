"""论文工作台：路径固化 + 文件名映射（workbench-state.json）。

P1 单一事实来源：项目首次进入论文工作台模块做文件读写时，把该项目的
详细目录（paths）+ 文件名映射（filemap）一次性固化到
``{pipeline_root}/papers/{paper_id}/workbench-state.json``。
此后所有读写基于固化路径 + 映射查取，**禁止每次临时拼接路径/文件名**
（大小写 / Unicode 规范化 / 代码页差异曾反复导致"文件不存在"、多轮排查）。

约定：
- 逻辑名 = 用户/前端可见名（可含中文，如 ``drafts/妇女史论文.md``）
- 物理名 = 系统编码不敏感唯一名：``{安全化stem}_{xxhash64(逻辑名)[:6]}{ext}``
  （确定性：同逻辑名永远同物理名；安全字符集 = 字母数字下划线中文连字符）
- 映射范围：drafts/ 工作副本 + output/ 交付件；
  **不含** _history/ 版本快照（保持原名）与 inputs/（用户证据链原名）
- 写入 UTF-8 + 原子写（tmp + os.replace）

workbench-state.json schema（U1 路径跨边界契约第 6 节登记）：

- ``paths``：7 个键的固化绝对路径（ensure 时一次性固化 + 自愈重固化）——
  ``project_root``（项目根目录）、``drafts``（草稿目录）、``output``（交付件目录）、
  ``history``（drafts/_history 版本快照目录）、``versions_log``（versions.jsonl
  版本日志**文件**）、``biblio``（bibliography.csl.json 引用清单**文件**）、
  ``inputs``（用户输入文件目录）。注意 versions_log/biblio 是文件路径，其余 5 个是目录。
- ``filemap``：逻辑相对路径 → 物理相对路径（``{逻辑名: 物理名}``）。物理名由
  xxhash64(逻辑名) 取前 6 位确定性生成——同逻辑名永远同物理名，可安全重放。
- ``inputs_log``：``[{name, ts, source}]``——inputs/ 每次提交的固化登记
  （name=文件名，ts=本地时间 ``%Y-%m-%dT%H:%M:%S``，source=upload/upload-dup/legacy
  等来源标记）；同名再提交只更新时间，不重复追加。
- 其余：``schema``（版本号）、``created_at`` / ``last_updated``（本地时间戳）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import xxhash

from pipeline_core.atomic import atomic_write

logger = logging.getLogger(__name__)

SCHEMA = 1

# 进程内读写串行化（2026-08-10）：uvicorn 线程池下并发 ensure/register 会同时
# atomic_write 同一个 workbench-state.json——os.replace 撞上另一线程的
# Python 读句柄（无 FILE_SHARE_DELETE）即 WinError 5。本模块全部读写走此锁
# （RLock：ensure 内 load→写 嵌套）；跨进程/杀毒软件瞬时锁由 atomic_write 的
# PermissionError 退避重试兜底。
import threading

_STATE_LOCK = threading.RLock()


def _write_state(paper_dir: Path, st: dict) -> None:
    with _STATE_LOCK:
        atomic_write(state_path(paper_dir), json.dumps(st, ensure_ascii=False, indent=2))

# 与 m0.compute_safe_filename 同规则（无时间戳，保证确定性）：保留中文/字母/数字/空格/连字符
_SAFE_STEM_RE = re.compile(r"[^\w\s\u4e00-\u9fff-]", re.UNICODE)


def _safe_stem(fname: str) -> str:
    stem = Path(fname).stem
    name = _SAFE_STEM_RE.sub("_", stem)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed"


def path_hash(logical_rel: str) -> str:
    """路径哈希：xxhash64(逻辑相对路径字符串) → 16 位小写 hex（S6 语义拆分）。

    语义边界：仅用于「逻辑名 → 确定性物理名」映射（同逻辑名永远同物理名）。
    输入是路径字符串而非文件内容——**禁止**当作内容判重哈希使用；
    内容判重 / doc_id 走 ``pipeline_core.doc_id.content_hash``。
    """
    return xxhash.xxh64(logical_rel.encode("utf-8")).hexdigest()


def physical_name(logical_rel: str) -> str:
    """逻辑相对路径（如 ``drafts/妇女史论文.md``）→ 物理相对路径（确定性哈希）。"""
    parent, fname = logical_rel.rsplit("/", 1)
    stem, ext = os.path.splitext(fname)
    h = path_hash(logical_rel)[:6]
    return f"{parent}/{_safe_stem(fname)}_{h}{ext}"


def state_path(paper_dir: Path) -> Path:
    return paper_dir / "workbench-state.json"


def load(paper_dir: Path) -> dict:
    """读固化 state（缺省返回空骨架，不抛错）。"""
    with _STATE_LOCK:
        p = state_path(paper_dir)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    return d
            except Exception:
                logger.warning("workbench-state 解析失败，重建: %s", p)
        return {"schema": SCHEMA, "created_at": "", "last_updated": "", "paths": {}, "filemap": {}}


def ensure(paper_dir: Path) -> dict:
    """首进/校验（幂等）：固化 paths + 迁移现存文件到物理名。

    并发安全：整体持模块锁（2026-08-10 竞态修复——并发 ensure 会在
    扫描→改名→落盘的间隙互踩：重复迁移/碰撞警告风暴/FileNotFoundError）。
    """
    with _STATE_LOCK:
        return _ensure_impl(paper_dir)


def _ensure_impl(paper_dir: Path) -> dict:
    """ensure 的实现体（调用方必须已持 _STATE_LOCK）。

    - paths：全目录 mkdir 后固化绝对路径（project_root/drafts/output/history/
      versions_log/biblio/inputs）
    - filemap：扫描 drafts/*.md 与 output/*.docx（顶层，不含 _history/）——
      已映射的跳过；未映射的按「逻辑名 = 当前文件名」建映射并 rename 到物理名
    - 自愈：固化 paths 与实际目录不符（目录移动等）时重新确定
    """
    st = load(paper_dir)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # paths：目录幂等创建 + 固化
    drafts = paper_dir / "drafts"
    output = paper_dir / "output"
    history = drafts / "_history"
    versions_log = paper_dir / "versions.jsonl"
    biblio = paper_dir / "bibliography.csl.json"
    inputs = paper_dir / "inputs"
    for d in (drafts, output, history, inputs):
        d.mkdir(parents=True, exist_ok=True)
    versions_log.touch(exist_ok=True)

    fresh_paths = {
        "project_root": str(paper_dir),
        "drafts": str(drafts),
        "output": str(output),
        "history": str(history),
        "versions_log": str(versions_log),
        "biblio": str(biblio),
        "inputs": str(inputs),
    }
    # 自愈：路径移动/变化时重固化（绝对路径即权威，不重新推导其它来源）
    if st.get("paths") != fresh_paths or not st.get("created_at"):
        st["paths"] = fresh_paths
        if not st.get("created_at"):
            st["created_at"] = now

    # filemap：现存物理文件 → 逻辑名（首次迁移或补齐）
    filemap = st.setdefault("filemap", {})
    reversed_map = {v: k for k, v in filemap.items()}
    changed = False
    # drafts/ 工作副本（顶层 *.md，排除 _history/）
    for f in sorted(drafts.glob("*.md")):
        if f.is_file():
            rel = f"drafts/{f.name}"
            if rel in reversed_map:
                continue  # 已映射
            logical_rel = rel  # 旧格式：原名即逻辑名
            phys_rel = physical_name(logical_rel)
            _rename_to(f, drafts / Path(phys_rel).name, logical_rel)
            filemap[logical_rel] = phys_rel
            reversed_map[phys_rel] = logical_rel
            changed = True
    # output/ 交付件（*.docx，排除隐藏临时文件）
    for f in sorted(output.glob("*.docx")):
        if f.is_file() and not f.name.startswith("."):
            rel = f"output/{f.name}"
            if rel in reversed_map:
                continue
            logical_rel = rel
            phys_rel = physical_name(logical_rel)
            _rename_to(f, output / Path(phys_rel).name, logical_rel)
            filemap[logical_rel] = phys_rel
            reversed_map[phys_rel] = logical_rel
            changed = True

    if changed or st.get("last_updated") != now:
        st["last_updated"] = now
        _write_state(paper_dir, st)
    return st


def _rename_to(f: Path, target: Path, logical_rel: str) -> None:
    """文件改名到物理名（同名跳过）。日志记录逻辑名 ↔ 物理名映射。"""
    if f.resolve() == target.resolve():
        return
    if target.exists():
        # 极端碰撞（物理名已被占用）：加序号保底
        i = 1
        while target.exists():
            target = target.with_name(f"{target.stem}_{i}{target.suffix}")
            i += 1
        logger.warning("物理名碰撞，使用 %s (logical=%s)", target.name, logical_rel)
    f.rename(target)
    logger.info("workbench filemap: %s → %s", logical_rel, target.name)


def phys_path(paper_dir: Path, st: dict, logical_rel: str) -> Path:
    """读场景：逻辑相对路径 → 物理 Path（映射必须已存在，缺失即文件不存在）。"""
    phys_rel = (st.get("filemap") or {}).get(logical_rel)
    if not phys_rel:
        raise FileNotFoundError(f"映射不存在: {logical_rel}（需先经 ensure 建立）")
    return paper_dir / phys_rel


def register(paper_dir: Path, st: dict, logical_rel: str) -> Path:
    """写场景：逻辑相对路径 → 物理 Path。确定性生成物理名 + 注册映射 + 落盘。"""
    with _STATE_LOCK:
        st = st or ensure(paper_dir)
        filemap = st.setdefault("filemap", {})
        if logical_rel not in filemap:
            filemap[logical_rel] = physical_name(logical_rel)
            st["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _write_state(paper_dir, st)
        return paper_dir / filemap[logical_rel]


def logical_name(paper_dir: Path, st: dict, physical_rel: str) -> str:
    """物理相对路径 → 逻辑相对路径（列表反查用）。未映射时按原名返回。"""
    for lr, pr in (st.get("filemap") or {}).items():
        if pr == physical_rel:
            return lr
    return physical_rel


def ensure_inputs_log(paper_dir: Path, st: dict) -> None:
    """为 inputs/ 现存文件补齐 inputs_log 初始记录（按文件 mtime，幂等）。

    inputs_log: [{name, ts, source}]——已提交文件的固化登记，不依赖会话/文件 mtime。
    """
    with _STATE_LOCK:
        inputs_dir = Path((st.get("paths") or {}).get("inputs", "")) if st.get("paths") else paper_dir / "inputs"
        if not inputs_dir.exists():
            return
        log = {r["name"]: r for r in (st.get("inputs_log") or [])}
        changed = False
        for f in sorted(inputs_dir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.name not in log:
                log[f.name] = {
                    "name": f.name,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime)),
                    "source": "legacy",
                }
                changed = True
        if changed:
            st["inputs_log"] = sorted(log.values(), key=lambda r: r["name"])
            _write_state(paper_dir, st)


def remove_inputs_log(paper_dir: Path, st: dict, name: str) -> bool:
    """从 inputs_log 移除指定文件登记（inputs 删除进 _trash 后调用）。返回是否有变更。"""
    with _STATE_LOCK:
        before = len(st.get("inputs_log") or [])
        st["inputs_log"] = [r for r in (st.get("inputs_log") or []) if r.get("name") != name]
        if len(st["inputs_log"]) == before:
            return False
        _write_state(paper_dir, st)
        return True


def set_input_doc_id(paper_dir: Path, st: dict, name: str, doc_id: str) -> bool:
    """为 inputs_log 中指定文件登记向量 doc_id（T1 参考资料入库后调用）。

    doc_id = 内容哈希（compute_doc_id），供 /search 检索结果标「本项目资料」徽标对照。
    """
    with _STATE_LOCK:
        changed = False
        for r in (st.get("inputs_log") or []):
            if r.get("name") == name and r.get("doc_id") != doc_id:
                r["doc_id"] = doc_id
                changed = True
        if changed:
            _write_state(paper_dir, st)
        return changed


def output_latest(paper_dir: Path, st: dict, draft_stem: str) -> dict | None:
    """output/ 下指定草稿的最新阶段交付件（按逻辑名版本号最大）。
    返回 {name: 逻辑文件名, path: 物理绝对路径}，无则 None。
    """
    prefix = f"output/{draft_stem}_v"
    best = None  # (version, logical_rel)
    for lr in (st.get("filemap") or {}):
        if lr.startswith(prefix):
            # 历史坑（U1 契约登记）：版本号必须先去 .docx 后缀再 isdigit——
            # 直接对 "3.docx" 调 isdigit 恒为 False，会导致最新版本永远解析不出。
            ver = lr[len(prefix):-len(".docx")]  # 去 .docx 后缀取纯版本号
            if ver.isdigit() and (best is None or int(ver) > best[0]):
                best = (int(ver), lr)
    if best is None:
        return None
    logical_rel = best[1]
    phys = paper_dir / (st["filemap"][logical_rel])
    return {"name": Path(logical_rel).name, "path": str(phys)}
