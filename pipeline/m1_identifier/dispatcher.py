"""M1 主调度器 — 扫描 00_待处理/ → 分类 → 页计划 → 首页归档 → sidecar 更新 → 分流。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import fitz

from pipeline_core.atomic import AtomicMoveError, atomic_move
from pipeline_core.config import PipelineConfig
from pipeline_core.doc_id import compute_doc_id
from pipeline_core.progress import report
from pipeline_core.retry import RetryExhaustedError, retry_on_failure
from pipeline_core.sidecar import read_sidecar, update_sidecar_field

from .classifier import classify_document, classify_pages
from .firstpage import extract_firstpage_text, render_firstpage
from .page_plan import build_page_plan, write_page_plan
from .router import move_to_queue, route_document

logger = logging.getLogger(__name__)


def _report_stage_progress(root: Path, doc_id: str, done: int, total: int) -> None:
    """进度条埋点（fail-safe：进度写入失败不得影响主流程）。"""
    try:
        report("m1", doc_id, done, total, root=root)
    except Exception:
        pass


def dispatch_all(root: Path, config: PipelineConfig) -> list[dict]:
    """扫描 00_待处理/，逐文件执行 M1 流程。"""
    queue_dir = root / "00_待处理"
    results: list[dict] = []

    # 进度条总数：本轮扫描到的全部待处理文件（与下方循环同一过滤口径）
    candidates = [
        f for f in sorted(queue_dir.iterdir())
        if f.is_file() and f.suffix != ".processing"
        and not f.name.startswith("[损坏]")  # E-1：损坏文件跳过（等用户 force/删除）
        and f.suffix.lower() in (".txt", ".md", ".jpg", ".jpeg", ".png", ".pdf")
    ]
    total_files = len(candidates)

    for file_idx, f in enumerate(candidates, start=1):

        # 非 PDF 路由（C1 §9）：txt/md 直入 03；图片入 01；其余跳过
        suffix = f.suffix.lower()
        if suffix in (".txt", ".md"):
            try:
                dest = atomic_move(f, root / "03_知识库原文")
                results.append({"doc_id": f.name, "status": "ok", "target": "03_知识库原文", "via": "direct"})
                logger.info(f"M1 直达: {f.name} → {dest}")
            except Exception as e:
                logger.error(f"M1 直达失败: {f.name}: {e}")
                results.append({"doc_id": f.name, "status": "error", "error": str(e)})
            _report_stage_progress(root, f.name, file_idx, total_files)
            continue
        if suffix in (".jpg", ".jpeg", ".png"):
            try:
                dest = atomic_move(f, root / "01_OCR队列")
                results.append({"doc_id": f.name, "status": "ok", "target": "01_OCR队列", "via": "image"})
                logger.info(f"M1 图片入队: {f.name} → {dest}")
            except Exception as e:
                logger.error(f"M1 图片入队失败: {f.name}: {e}")
                results.append({"doc_id": f.name, "status": "error", "error": str(e)})
            _report_stage_progress(root, f.name, file_idx, total_files)
            continue
        if suffix != ".pdf":
            continue

        # E-1：损坏检测前置——fitz 打不开的文件直接标记回 00_待处理，
        # 不进 retry（retry 的多次 atomic_move + fitz.open 在 Windows 下
        # 会产生句柄竞争 WinError 32，且损坏文件重试无意义）。
        # 注意：fitz.open 抛的异常对象持有 mupdf 文件句柄，须在 except 块
        # 结束后（异常对象出作用域）再执行标记，否则 WinError 32。
        corrupt = False
        try:
            import fitz as _fitz
            _tmp_doc = _fitz.open(str(f))
            _tmp_doc.close()
            del _tmp_doc
        except Exception:
            corrupt = True

        if corrupt:
            logger.warning(f"M1 损坏文件: {f.name}，标记 [损坏] 回待处理")
            try:
                _mark_corrupt_pending(root, f)
            except Exception:
                pass
            results.append({"doc_id": f.name, "status": "corrupt", "error": "PDF 损坏，已标记待用户处理"})
            _report_stage_progress(root, f.name, file_idx, total_files)
            continue

        try:
            result = _retryable_process_one(root, config, f)
            results.append(result)
        except RetryExhaustedError as e:
            logger.error(f"M1 重试耗尽: {f.name}: {e}")
            try:
                # E-1：损坏 PDF → 标记 [损坏] 移回 00_待处理（用户可强制处理/删除）；
                # 非损坏（临时错误）→ 仍走 99_异常
                if _is_corrupt_pdf(f):
                    _mark_corrupt_pending(root, f)
                else:
                    _move_to_error(root, f, str(e.last_error or e))
            except Exception:
                pass
            results.append({"doc_id": f.name, "status": "error", "error": str(e)})
        except Exception as e:
            logger.error(f"M1 处理失败: {f.name}: {e}")
            # E-1：fitz 打开失败（损坏）→ 标记 [损坏] 回 00_待处理
            try:
                if _is_corrupt_pdf(f):
                    _mark_corrupt_pending(root, f)
            except Exception:
                pass
            results.append({"doc_id": f.name, "status": "error", "error": str(e)})
        _report_stage_progress(root, results[-1].get("doc_id", f.name), file_idx, total_files)

    return results


@retry_on_failure(
    max_retries=3,
    backoff_s=1.0,
    backoff_multiplier=2.0,
    retryable_exceptions=(Exception,),
)
def _retryable_process_one(root: Path, config: PipelineConfig, pdf_path: Path) -> dict:
    """可重试的单文件处理包装。"""
    return _process_one(root, config, pdf_path)


def _process_one(root: Path, config: PipelineConfig, pdf_path: Path) -> dict:
    """处理单个 PDF 文件。"""
    now = datetime.now(timezone.utc).isoformat()

    # 原子取件
    processing = atomic_move(pdf_path, root / "00_待处理")
    doc = fitz.open(str(processing))

    try:
        doc_id = compute_doc_id(processing)
        sidecar = read_sidecar(root / "sidecar", doc_id)
        thresholds = {
            "page_text_min_chars": config.thresholds.page_text_min_chars,
            "image_area_ratio": config.thresholds.image_area_ratio,
            "doc_type_ratio": config.thresholds.doc_type_ratio,
            "sample_pages": config.thresholds.sample_pages,
        }

        # 1) 收集页信息用于分类
        pages_info = []
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text("text")
            char_count = len(text.replace("\n", "").replace(" ", ""))
            pages_info.append({
                "page": i + 1,
                "text": text,
                "char_count": char_count,
                # E-2 修订：有文本层即非图片页（不再用 50 字符硬阈值误伤短文本）
                "is_image_page": char_count == 0,
            })

        # 2) 文档级分类
        classification = classify_document(pages_info, thresholds)

        # 3) 逐页 action
        page_actions = classify_pages(doc, thresholds)

        # 4) 生成页计划
        plan = build_page_plan(
            doc_id=doc_id,
            source_file=sidecar.source_file,
            total_pages=len(doc),
            classification=classification,
            pages=page_actions,
            translate_flag=sidecar.translate_flag,
        )
        write_page_plan(root / "plans", plan)

        # 5) 首页归档
        firstpage_png = root / "sidecar" / f"{doc_id}_firstpage.png"
        render_firstpage(doc, firstpage_png)

        firstpage_txt = root / "sidecar" / f"{doc_id}_firstpage.txt"
        fp_text = extract_firstpage_text(doc)
        firstpage_txt.write_text(fp_text, encoding="utf-8")

        # 6) 更新 sidecar
        lang = classification.language
        tl_flag = sidecar.translate_flag
        conflict_note = None
        if lang == "A" and tl_flag:
            conflict_note = f"语种判定为A(CJK>{thresholds['doc_type_ratio']*100:.0f}%)但[T]标记存在，路由以[T]为准"
        elif lang == "B" and not tl_flag:
            conflict_note = f"语种判定为B(CJK<10%)但无[T]标记，可能需翻译"

        update_kwargs = {
            "classification": {"composition": classification.composition, "language": lang},
            "stage_ts": {**sidecar.stage_ts, "m1": now},
            "engines": {**sidecar.engines, "identifier": f"pymupdf-{fitz.version[0]}"},
        }
        if conflict_note:
            update_kwargs["error"] = conflict_note

        update_sidecar_field(root / "sidecar", doc_id, **update_kwargs)

        # 7) 分流前必须先关闭文档句柄——Windows 下持句柄移动文件会触发
        # atomic 的 copy+delete 降级，导致源文件残留在原队列被反复重处理
        doc.close()

        # 8) 分流 — 组装方裁定：纯文本无[T] → 01_OCR队列（M2 直抽文本产出 MD）
        target = route_document(root, processing, plan, sidecar.translate_flag)
        move_to_queue(root, processing, target)

        logger.info(f"M1 完成: {doc_id} → {target}")
        return {"doc_id": doc_id, "status": "ok", "target": target}

    finally:
        # 兜底关闭（异常路径；正常路径已在移送前关闭）
        try:
            doc.close()
        except Exception:
            pass


def _move_to_error(root: Path, pdf_path: Path, error_msg: str):
    """移入 99_异常/ + 写 error.txt。"""
    err_dir = root / "99_异常"
    err_dir.mkdir(parents=True, exist_ok=True)
    try:
        dest = atomic_move(pdf_path, err_dir)
        err_path = err_dir / f"{dest.stem}.error.txt"
        err_path.write_text(error_msg, encoding="utf-8")
    except AtomicMoveError:
        pass


def _is_corrupt_pdf(path: Path) -> bool:
    """E-1：判断 PDF 是否损坏（fitz 打不开）。"""
    if path.suffix.lower() != ".pdf":
        return False
    try:
        import fitz
        doc = fitz.open(str(path))
        doc.close()
        return False
    except Exception:
        return True


def _mark_corrupt_pending(root: Path, pdf_path: Path) -> None:
    """E-1：损坏文件加 [损坏] 前缀，留在 00_待处理（M1 跳过，等用户强制/删除）。

    Windows 瞬时锁重试：文件刚上传/rename 后，Defender/索引服务可能瞬时
    占用句柄（WinError 32），os.replace 失败后 sleep 重试。
    """
    if pdf_path.name.startswith("[损坏]"):
        return
    new_path = pdf_path.with_name(f"[损坏]{pdf_path.name}")
    import os
    import time
    for attempt in range(3):
        try:
            os.replace(str(pdf_path), str(new_path))
            logger.warning(f"M1 损坏标记: {pdf_path.name} → [损坏]{pdf_path.name}（等待用户处理）")
            return
        except OSError as e:
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
            else:
                logger.error(f"M1 损坏标记失败（重试 3 次）: {pdf_path.name}: {e}")
