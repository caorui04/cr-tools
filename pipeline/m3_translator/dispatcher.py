"""M3 主调度器 — 扫描 02_翻译队列/ → 切块→生成块计划→翻译→双语MD→流转。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline_core.atomic import atomic_move, recover_orphaned_processing
from pipeline_core.doc_id import compute_doc_id
from pipeline_core.progress import report
from pipeline_core.retry import retry_on_failure
from pipeline_core.sidecar import read_sidecar, update_sidecar_field

from pipeline_core.stage_progress import STAGE_TRANSLATE, clear_progress, load_progress, save_progress

from .translator import Block, build_bilingual_md, parse_md_blocks

logger = logging.getLogger(__name__)


def _report_stage_progress(root: Path, doc_id: str, done: int, total: int) -> None:
    """进度条埋点（fail-safe：进度写入失败不得影响主流程）。"""
    try:
        report("m3", doc_id, done, total, root=root)
    except Exception:
        pass


def process_document(root: Path, md_path: Path, provider=None) -> Path | None:
    """处理单个 .ocr.md / .md：切块→计划→翻译→输出→流转。"""
    processing = atomic_move(md_path, root / "02_翻译队列")
    text = processing.read_text(encoding="utf-8")

    # 从文件名推断 doc_id（通过 sidecar 查找）
    doc_id = None
    sidecar_dir = root / "sidecar"
    if sidecar_dir.exists():
        for scf in sorted(sidecar_dir.glob("*.json")):
            try:
                sc = read_sidecar(sidecar_dir, scf.stem)
                if sc.source_file in processing.name or processing.stem in sc.source_file:
                    doc_id = sc.doc_id
                    break
            except Exception:
                continue
    if doc_id is None:
        doc_id = processing.stem[:12]

    thresholds = {"lang_foreign_ratio": 0.80, "block_min_chars": 50}

    # 1) 切块
    blocks = parse_md_blocks(text, thresholds)

    # 2) 生成块计划 JSON
    plan = {
        "doc_id": doc_id,
        "source_file": processing.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "blocks": [
            {
                "seq": b.seq, "type": b.type, "action": b.action,
                "lang_foreign_ratio": b.lang_foreign_ratio, "char_count": b.char_count,
            }
            for b in blocks
        ],
    }
    plan_dir = root / "plans"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{doc_id}_translate_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) 断点续传 — 加载已完成块 seq（方案C：恢复时重新翻译，不存译文）
    completed_seqs = load_progress(root, doc_id, STAGE_TRANSLATE)
    if completed_seqs:
        logger.info(f"M3 断点续传: {doc_id} 已完成 {len(completed_seqs)} 块，重新翻译")

    # 4) 翻译
    if provider:
        import re as _re
        # 页码标记保护（M3-5）：Hy-MT2 会把 <!-- page N --> 译为 <!-- 第N页 -->，
        # 破坏 chunk 页码解析。翻译前替换为占位符，翻译后恢复——LLM 不守指令也破坏不了。
        _PAGE_RE = _re.compile(r"<!--\s*page\s+(\d+)\s*-->")
        translate_blocks = [b for b in blocks if b.action == "translate"]
        total_blocks = len(translate_blocks)
        done_blocks = 0
        for b in translate_blocks:
            page_markers: dict[str, str] = {}

            def _protect(m: _re.Match) -> str:
                ph = f"__PAGE_MARKER_{len(page_markers)}_{m.group(1)}__"
                page_markers[ph] = f"<!-- page {m.group(1)} -->"
                return ph

            text = _PAGE_RE.sub(_protect, b.text)
            try:
                translated = provider.translate(text, "en", "zh")
                for ph, orig in page_markers.items():
                    translated = translated.replace(ph, orig)
                b.translated = translated
            except Exception as e:
                logger.warning(f"翻译块 {b.seq} 失败: {e}")
                b.translated = f"[翻译失败] {b.text}"
            # 记录完成块
            save_progress(root, doc_id, STAGE_TRANSLATE, b.seq)
            # 进度条：已译块 / 总块数
            done_blocks += 1
            _report_stage_progress(root, doc_id, done_blocks, total_blocks)

    # 5) 清除本环节断点，生成双语 MD（临时写入 + 原子改名 + atomic_move 流转）
    clear_progress(root, doc_id, STAGE_TRANSLATE)
    bilingual = build_bilingual_md(blocks)
    # 仅摘除文件名前缀 [T]（C1 §3: [T] 生命周期由 M0 写入，M3 消费后摘除）
    stem = processing.stem
    if stem.startswith("[T]"):
        stem = stem[3:]
    out_name = stem + ".bilingual.md"

    # 临时文件写入（崩溃安全：残留 .tmp 可被清理）
    tmp_fd, tmp_path = tempfile.mkstemp(dir=root / "02_翻译队列", suffix=".md")
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        f.write(bilingual)
    out_path = root / "02_翻译队列" / out_name
    os.replace(tmp_path, out_path)  # 同盘原子改名

    # 6) 流转到 03_知识库原文/（atomic_move 提供 .processing 协议保护）
    final = atomic_move(out_path, root / "03_知识库原文")

    # 7) 归档源 .ocr.md（M3-1 修复：内容已并入 bilingual.md，源残留 02
    #    会被 watcher 每 5s 反复处理 → 重复翻译 + 覆盖 bilingual）
    try:
        archive_dir = root / "04_已入库归档" / datetime.now().strftime("%Y-%m") / "原文md"
        archive_dir.mkdir(parents=True, exist_ok=True)
        atomic_move(processing, archive_dir)
        logger.info(f"M3 源归档: {processing.name} → {archive_dir}")
    except Exception as e:
        logger.warning(f"M3 源归档失败（将残留队列被再次处理）: {e}")

    # 8) 更新 sidecar
    if doc_id:
        try:
            sc = read_sidecar(sidecar_dir, doc_id)
            now = datetime.now(timezone.utc).isoformat()
            update_sidecar_field(
                sidecar_dir, doc_id,
                stage_ts={**sc.stage_ts, "m3": now},
                engines={**sc.engines, "translator": provider.name if provider else "stub"},
            )
        except Exception:
            pass

    logger.info(f"M3 完成: {doc_id} → {final}")
    return final


def dispatch_all(root: Path, provider=None) -> dict:
    """扫描 02_翻译队列/，逐文件处理。"""
    recover_orphaned_processing(root)
    qdir = root / "02_翻译队列"
    if not qdir.exists():
        return {"processed": 0, "errors": []}

    processed = 0
    errors: list[str] = []

    for f in sorted(qdir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix == ".processing":
            continue
        if not (f.suffix == ".md" or f.name.endswith(".ocr.md")):
            continue
        try:
            result = process_document(root, f, provider)
            if result:
                processed += 1
        except Exception as e:
            logger.error(f"M3 失败: {f.name}: {e}")
            errors.append(str(e))

    return {"processed": processed, "errors": errors}
