"""M2 OCR 子进程（方案 B+：子进程化）。

由 kb-pipeline 主进程通过 `--ocr-worker <root> <doc_id> <pdf_path>` 拉起。
职责：读页计划 → 逐页 extract/OCR → 进度上报 → 生成 .ocr.md → 更新 sidecar。

stdout 行协议（每行一个 JSON，UTF-8）：
  {"type":"heartbeat","ts":1785460000}
  {"type":"progress","page":3,"total":8}
  {"type":"done","pages":8,"md":"xxx.ocr.md"}
  {"type":"error","msg":"..."}

父进程（dispatcher.py）负责：心跳超时 → taskkill /F /T；
done → 校验 .ocr.md 存在并流转到下一队列。

设计要点：
- 子进程每次只处理 1 个文档（内存安全）
- 页面级断点续传：progress/<doc_id>.ocr.progress 逐页追加（D6 stage 命名空间）
- stdout 行协议每行 JSON，与 pi-agent RPC 格式一致
- Windows 下 stdout 强制 UTF-8（默认 GBK 会破坏行协议）
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _emit(payload: dict) -> None:
    """stdout 行协议输出（flush 保证实时可见）。"""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _save_asset(root: Path, doc_id: str, page_num: int, doc,
                bbox: list[float], img_idx: int) -> str | None:
    """抽取 keep_asset 图像到 assets/<doc_id>/p{页}_{序号}.png。"""
    try:
        asset_dir = root / "assets" / doc_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        page = doc[page_num - 1]
        rect = fitz.Rect(*bbox)
        pix = page.get_pixmap(clip=rect, dpi=150)
        out = asset_dir / f"p{page_num}_{img_idx}.png"
        pix.save(str(out))
        return f"assets/{doc_id}/p{page_num}_{img_idx}.png"
    except Exception:
        return None


def _ensure_utf8_stdout() -> None:
    """Windows 控制台默认 GBK；行协议必须 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ocr_worker_main(root: Path, doc_id: str, pdf_path: Path) -> int:
    """子进程主流程。返回 0=成功, 1=失败。"""
    import fitz

    from pipeline_core.progress import report
    from pipeline_core.sidecar import read_sidecar, update_sidecar_field
    from pipeline_core.stage_progress import STAGE_OCR, clear_progress, save_progress

    from .ocr_engine import OCREngine, build_ocr_md, render_page_for_ocr

    _emit({"type": "heartbeat", "ts": int(time.time())})
    doc = None
    try:
        doc = fitz.open(str(pdf_path))

        # 1) sidecar + 页计划
        sidecar = read_sidecar(root / "sidecar", doc_id)
        plan_path = root / "plans" / f"{doc_id}_page_plan.json"
        if not plan_path.exists():
            _emit({"type": "error", "msg": "页计划文件缺失"})
            return 1
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        pages_data = plan.get("pages", [])

        # 2) 断点续传（E-3）：从 partial 中间文件恢复已完成页文本
        #    方案：逐页处理时追加写 progress/<doc_id>.partial.ocr.md，
        #    崩溃重启后读回已完成页，跳过重跑（不丢内容）。
        partial_path = root / "progress" / f"{doc_id}.partial.ocr.md"
        resumed_pages: dict[int, str] = {}
        if partial_path.exists():
            try:
                text = partial_path.read_text(encoding="utf-8")
                for m in re.finditer(r"<!-- page (\d+) -->\n(.*?)(?=\n<!-- page |\Z)", text, re.S):
                    resumed_pages[int(m.group(1))] = m.group(2)
                if resumed_pages:
                    _emit({"type": "resume", "completed": sorted(resumed_pages)})
                    logger.info(f"M2 断点续传: {doc_id} 已恢复 {len(resumed_pages)} 页")
            except Exception as e:
                logger.warning(f"M2 断点恢复失败（从头处理）: {e}")

        # 3) 逐页处理
        ocr_engine = OCREngine()
        pages_md: list[tuple[int, str]] = []
        low_confs: list[float] = []
        total = len(pages_data)

        # partial 追加锁（单 worker 串行，无需真锁）
        partial_file = None
        if partial_path.parent.exists():
            partial_file = open(partial_path, "a", encoding="utf-8")

        for page_idx, entry in enumerate(pages_data, start=1):
            page_num = entry["page"]
            action = entry["action"]
            images = entry.get("images", [])

            if page_num in resumed_pages:
                # 断点恢复页：直接采用已存文本
                pages_md.append((page_num, resumed_pages[page_num]))
                if partial_file:
                    partial_file.write(f"<!-- page {page_num} -->\n{resumed_pages[page_num]}\n")
                    partial_file.flush()
                _emit({"type": "progress", "page": page_idx, "total": total, "resumed": True})
                continue

            if action == "extract":
                page = doc[page_num - 1]
                text = page.get_text("text")
                for img in images:
                    if img.get("action") == "keep_asset":
                        bbox = img["bbox"]
                        asset_path = _save_asset(root, doc_id, page_num, doc, bbox, len(images))
                        if asset_path:
                            text += f"\n![图]({asset_path})\n"
                pages_md.append((page_num, text))

            elif action == "ocr":
                # 2026-08-16 会话 9 修复：OCR DPI 300 → 150（实测单页 150DPI ≈12s，
                # 300DPI 复杂页可 >180s → dispatcher 心跳超时杀 worker → 扫描件全部入 99_异常）
                img_array = render_page_for_ocr(doc, page_num, dpi=150)
                ocr_text, conf = ocr_engine.ocr_page_with_confidence(img_array)
                pages_md.append((page_num, ocr_text))
                low_confs.append(conf)

            # 断点续传：每页完成即追加 partial（崩溃后恢复用）
            if partial_file:
                try:
                    partial_file.write(f"<!-- page {page_num} -->\n{pages_md[-1][1]}\n")
                    # 2026-08-17 会话 11 修复：write 后 flush——Python 默认块缓冲下
                    # 内容停留在内存缓冲，崩溃/被杀时断点数据丢失（实测运行中 partial 0 字节）
                    partial_file.flush()
                except Exception as e:
                    logger.warning(f"partial 写入失败: {e}")
            # 进度上报（fail-safe）
            try:
                save_progress(root, doc_id, STAGE_OCR, page_num)
            except Exception as e:
                logger.warning(f"save_progress 失败: {e}")
            try:
                report("m2", doc_id, page_idx, total, root=root)
            except Exception:
                pass
            _emit({"type": "progress", "page": page_idx, "total": total})
            _emit({"type": "heartbeat", "ts": int(time.time())})

        # 关闭 partial 文件句柄
        if partial_file:
            try:
                partial_file.close()
            except Exception:
                pass

        # 3) 生成 .ocr.md（临时写入 + 原子改名）
        translate_flag = sidecar.translate_flag
        source_name = Path(sidecar.source_file).stem
        ocr_name = f"{'[T]' if translate_flag else ''}{source_name}.ocr.md"
        ocr_content = build_ocr_md(pages_md, sidecar.source_file)

        tmp_fd, tmp_path = tempfile.mkstemp(dir=root / "01_OCR队列", suffix=".ocr.md.tmp")
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(ocr_content)
        out_path = root / "01_OCR队列" / ocr_name
        os.replace(tmp_path, out_path)

        # 4) 更新 sidecar
        now = datetime.now(timezone.utc).isoformat()
        avg_conf = sum(low_confs) / len(low_confs) if low_confs else 1.0
        update_sidecar_field(
            root / "sidecar", doc_id,
            stage_ts={**sidecar.stage_ts, "m2": now},
            engines={**sidecar.engines, "ocr": "paddleocr-ppstructv3"},
            low_confidence=avg_conf < 0.85,
        )

        # 5) 清除本环节断点 + partial 中间文件，emit done
        clear_progress(root, doc_id, STAGE_OCR)
        try:
            partial_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"partial 清理失败: {e}")
        try:
            doc.close()
        except Exception:
            pass
        _emit({"type": "done", "pages": total, "md": ocr_name})
        return 0

    except Exception as e:
        # 异常路径：关闭 partial 句柄（保留文件供断点恢复）
        try:
            if partial_file:
                partial_file.close()
        except Exception:
            pass
        _emit({"type": "error", "msg": str(e)})
        return 1
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def main(argv: list[str]) -> int:
    """CLI 入口：--ocr-worker <root> <doc_id> <pdf_path>。"""
    _ensure_utf8_stdout()
    if len(argv) < 3:
        _emit({"type": "error", "msg": f"参数不足: --ocr-worker <root> <doc_id> <pdf_path> (got {argv})"})
        return 1
    root = Path(argv[0])
    doc_id = argv[1]
    pdf_path = Path(argv[2])
    return ocr_worker_main(root, doc_id, pdf_path)


if __name__ == "__main__":
    # 2026-08-17 会话 11（方案 B）：独立模块入口，绕开 server.main 的
    # fastapi/watcher 重型 import 链——Paddle C++ 在复杂进程环境下静默硬退出
    # （a6fff GB/T 国标证据链）。用法：python -m m2_ocr.ocr_worker <root> <doc_id> <pdf>
    sys.exit(main(sys.argv[1:]))
