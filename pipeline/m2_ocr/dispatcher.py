"""M2 主调度器（方案 B+：子进程化）— 扫描 01_OCR队列/ → spawn OCR 子进程 → 心跳监控 → 流转。

修复 M2-1：原实现单线程串行执行 PaddleOCR，某页 C 层卡死后整个 M2 watcher
永久阻塞。现改为子进程隔离：OCR 在子进程跑，父进程读 stdout 行协议
（heartbeat/progress/done/error），心跳超时 180s → taskkill /F /T 杀进程树，
部分产出（progress 断点）保留，重试由 sidecar.retry_count 控制（≥3 → 99_异常）。
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pipeline_core.atomic import atomic_move, recover_orphaned_processing
from pipeline_core.doc_id import compute_doc_id
from pipeline_core.sidecar import read_sidecar, update_sidecar_field

logger = logging.getLogger(__name__)

# 页级超时（秒）：2026-08-17 会话 11（方案 1）——自收到 progress 行起无新页进展
# 超过此时限判该页超时（杀 worker 重试）。取代原 HEARTBEAT_TIMEOUT_S=600：
# a6fff GB/T 页 9（1031 表格线矢量复杂版面）在 CPU 热降频下推理时长可从 88s
# 膨胀到 >20min（接近死循环），600s 心跳必然误杀；30min 上限覆盖极端慢页，
# 超过才是真卡死。heartbeat 行不再刷新计时（非协议行/心跳均不算页进展）。
PAGETIMEOUT_S = 1800.0
# 同一页连续超时 ≥ 2 次 → 跳过该页（占位入 partial，断点恢复视为已完成），
# 文档继续流转，不再死循环。
PAGE_TIMEOUT_SKIP_THRESHOLD = 2
# 单文档重试上限（C1 §3：重试 ≥3 次失败移入 99_异常/）
RETRY_MAX = 3
# Windows CREATE_NO_WINDOW：子进程不弹黑窗
_CREATE_NO_WINDOW = 0x08000000

# 页超时连续计数（进程内状态，watcher 单线程无并发问题）
_page_timeout_state: dict = {"page": None, "count": 0}


class _WorkerFailed(Exception):
    """子进程失败/超时，携带原因。"""


def _move_to_error(root: Path, pdf_path: Path, error_msg: str) -> None:
    """移入 99_异常/ + 写 error.txt（与 M1 一致）。"""
    err_dir = root / "99_异常"
    err_dir.mkdir(parents=True, exist_ok=True)
    try:
        dest = atomic_move(pdf_path, err_dir)
        err_path = err_dir / f"{dest.stem}.error.txt"
        err_path.write_text(error_msg, encoding="utf-8")
    except Exception as e:
        logger.error(f"移入 99_异常 失败: {pdf_path}: {e}")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Windows: taskkill /F /T 杀进程树（PaddleOCR C 层死锁 kill 不掉单进程）。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=15,
        )
    except Exception as e:
        logger.warning(f"taskkill 失败: {e}")
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def _spawn_ocr_worker(root: Path, doc_id: str, pdf_path: Path) -> subprocess.Popen:
    """spawn OCR 子进程。

    - 打包模式（sys.frozen）：sys.executable = kb-pipeline.exe，直接传参自调用
    - 源码模式：`python -m m2_ocr.ocr_worker <root> <doc_id> <pdf>`——2026-08-17
      会话 11（方案 B）：原 `-m server.main --ocr-worker ...` 会先加载 server.main
      全模块（fastapi/watcher 重型 import 链），Paddle C++ 在此环境下静默硬退出
      （a6fff GB/T 国标证据链：无 WER/无 traceback/无 error 消息）。改走轻量
      模块入口后 21 页全量直调稳定。

    源码/打包的路径差异是本函数唯一要点：两种模式 argv 到达 worker 入口时
    结构完全一致（root, doc_id, pdf_path）。
    """
    if getattr(sys, "frozen", False):
        base_cmd = [sys.executable]
    else:
        base_cmd = [sys.executable, "-m", "m2_ocr.ocr_worker"]
    cmd = [*base_cmd, str(root), doc_id, str(pdf_path)]
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stderr_log = open(log_dir / f"m2_worker_{doc_id}.log", "ab", buffering=0)

    creationflags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    # 2026-08-16 会话 9 修复：FLAGS_use_mkldnn=0 必须在 worker 进程启动时注入——
    # ocr_engine._load 内设置 env 太晚（server.main 入口链已 import paddle，flags 固化）。
    # oneDNN CPU 算子在部分扫描件（GB/T 国标）推理 segfault，禁用后实测 21 页全过。
    worker_env = dict(os.environ)
    worker_env.setdefault("FLAGS_use_mkldnn", "0")
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=stderr_log,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        env=worker_env,
    )


def _watch_worker(proc: subprocess.Popen) -> tuple[str, str | None, str | None, int | None]:
    """读 stdout 行协议直到 done/error/eof 或页级超时。

    Windows 下 select 不适用于管道，改用 reader 线程 + queue 轮询。

    Returns:
        (status, md_name, error_msg, page)
        status: "done" | "error" | "timeout" | "crashed" | "page_timeout"
        page: page_timeout 时返回卡住页号，其余为 None
    """
    q: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                q.put(("line", line))
        except Exception:
            pass
        finally:
            q.put(("eof", None))

    threading.Thread(target=reader, daemon=True, name="m2-stdout-reader").start()

    # 页级计时：仅 progress 行刷新；心跳/非协议行不算页进展
    last_progress_ts = time.time()
    current_page: int | None = None
    while True:
        try:
            kind, payload = q.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                # 进程已退出但 reader 未投递 eof（异常路径）
                return ("crashed", None, f"子进程提前退出（exit={proc.returncode}）", None)
            if time.time() - last_progress_ts > PAGETIMEOUT_S:
                _kill_tree(proc)
                return (
                    "page_timeout", None,
                    f"页 {current_page} 超时 {PAGETIMEOUT_S:.0f}s（无页进展），已杀进程树",
                    current_page,
                )
            continue

        if kind == "eof":
            return ("crashed", None, "子进程 stdout 关闭（未收到 done）", None)

        line = (payload or "").strip()
        if not line:
            continue
        try:
            import json
            data = json.loads(line)
        except json.JSONDecodeError:
            # 非协议行（Paddle 噪音如 ReduceMean...）——容忍，不刷新页计时
            logger.warning(f"M2 worker 非协议输出: {line[:200]}")
            continue

        msg_type = data.get("type")
        if msg_type == "done":
            return ("done", data.get("md"), None, None)
        if msg_type == "error":
            _kill_tree(proc)
            return ("error", None, data.get("msg") or "子进程报错", None)
        if msg_type == "progress":
            last_progress_ts = time.time()
            current_page = data.get("page")
        # heartbeat：仅刷新活动（不刷新页计时）——页计时由 progress 驱动


def _finalize(root: Path, processing: Path, md_path: Path, doc_id: str) -> Path:
    """子进程 done 后：校验 → 流转 .ocr.md → 归档原始 PDF。"""
    target = "03_知识库原文" if not md_path.name.startswith("[T]") else "02_翻译队列"
    final = atomic_move(md_path, root / target)
    archive_dir = root / "04_已入库归档" / datetime.now().strftime("%Y-%m") / "原始文件"
    archive_dir.mkdir(parents=True, exist_ok=True)
    atomic_move(processing, archive_dir)
    logger.info(f"M2 完成: {doc_id} → {final}")
    return final


def _skip_page(root: Path, doc_id: str, page_num: int) -> None:
    """跳过复杂页：向 partial 追加占位（断点恢复时 worker 视为已完成）。

    占位文本会以页内容形式进入最终 .ocr.md（标注跳过原因），对用户可见。
    """
    partial = root / "progress" / f"{doc_id}.partial.ocr.md"
    try:
        partial.parent.mkdir(parents=True, exist_ok=True)
        with open(partial, "a", encoding="utf-8") as f:
            f.write(f"<!-- page {page_num} -->\n<!-- OCR_SKIPPED: 复杂版面两次超时，跳过识别 -->\n")
        logger.warning(f"M2 跳过复杂页: {doc_id} page {page_num}（已写入 partial 占位）")
    except Exception as e:
        logger.error(f"M2 跳过页写入 partial 失败: {doc_id} page {page_num}: {e}")


def _handle_page_timeout(root: Path, doc_id: str, page: int | None) -> None:
    """页级超时容错：同一页连续 2 次超时 → 占位跳过；否则重试。

    与 retry_count 解耦（页超时是容错路径，不消耗重试上限），
    文档不会被页超时移入 99_异常。
    """
    global _page_timeout_state
    if page is None:
        return
    if _page_timeout_state["page"] == page:
        _page_timeout_state["count"] += 1
    else:
        _page_timeout_state = {"page": page, "count": 1}
    if _page_timeout_state["count"] >= PAGE_TIMEOUT_SKIP_THRESHOLD:
        _skip_page(root, doc_id, page)
        _page_timeout_state = {"page": None, "count": 0}
    else:
        logger.warning(f"M2 页 {page} 超时第 {_page_timeout_state['count']} 次: {doc_id}（重试，不消耗 retry）")


def _bump_retry(root: Path, doc_id: str, pdf_path: Path, error_msg: str) -> None:
    """失败路径：sidecar.retry_count +1；≥RETRY_MAX → 移 99_异常。"""
    try:
        sidecar = read_sidecar(root / "sidecar", doc_id)
        retry_count = sidecar.retry_count + 1
    except Exception as e:
        logger.error(f"读取 sidecar 失败（按重试 1 次处理）: {e}")
        retry_count = 1

    try:
        if retry_count >= RETRY_MAX:
            update_sidecar_field(root / "sidecar", doc_id, retry_count=retry_count, error=error_msg)
            _move_to_error(root, pdf_path, f"M2 重试 {retry_count} 次仍失败: {error_msg}")
            logger.error(f"M2 放弃: {doc_id}（重试 {retry_count} 次）→ 99_异常")
        else:
            update_sidecar_field(root / "sidecar", doc_id, retry_count=retry_count, error=error_msg)
            logger.warning(f"M2 重试 {retry_count}/{RETRY_MAX}: {doc_id}: {error_msg}")
    except Exception as e:
        logger.error(f"重试计数更新失败: {doc_id}: {e}")


def process_document(root: Path, pdf_path: Path) -> Path | None:
    """处理单个 PDF：spawn 子进程 → 心跳监控 → done 后流转。失败返回 None。"""
    # 取件（原子移动：短暂 .processing 防并发重入）
    processing = atomic_move(pdf_path, root / "01_OCR队列")
    doc_id = compute_doc_id(processing)

    try:
        sidecar = read_sidecar(root / "sidecar", doc_id)
        if sidecar.retry_count >= RETRY_MAX:
            logger.warning(f"M2 跳过（已达重试上限）: {doc_id}")
            _move_to_error(root, processing, f"M2 重试 {sidecar.retry_count} 次仍失败")
            return None
    except Exception as e:
        logger.warning(f"sidecar 读取失败（继续处理）: {doc_id}: {e}")

    proc = _spawn_ocr_worker(root, doc_id, processing)
    try:
        status, md_name, error_msg, timeout_page = _watch_worker(proc)
    finally:
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except Exception:
            pass

    if status == "done":
        md_path = root / "01_OCR队列" / (md_name or "")
        if not md_path.exists():
            _bump_retry(root, doc_id, processing, f".ocr.md 未生成: {md_name}")
            return None
        try:
            return _finalize(root, processing, md_path, doc_id)
        except Exception as e:
            _bump_retry(root, doc_id, processing, f"流转失败: {e}")
            return None

    if status == "page_timeout":
        # 页级超时容错：连续 2 次跳过该页；PDF 留队列，下 tick 断点续传重试
        _handle_page_timeout(root, doc_id, timeout_page)
        return None

    _bump_retry(root, doc_id, processing, error_msg or f"worker 异常退出（{status}）")
    return None


def dispatch_all(root: Path) -> dict:
    """扫描 01_OCR队列/，每次调用最多处理 1 个文档（内存安全：一次一个 OCR 进程）。

    队列中多于 1 个文档时由后续 tick（watcher interval=5s）逐个消费。

    Worker 互斥锁（2026-08-01）：spawn 前获取锁，已有活跃 worker（如孤儿进程
    或并行 tick）则跳过本次——防 1.7GB × N 内存叠加。
    """
    recover_orphaned_processing(root)
    qdir = root / "01_OCR队列"
    if not qdir.exists():
        return {"processed": 0, "errors": []}

    # 互斥锁：拿不到（已有活跃 worker）→ 跳过，等下个 tick
    from pipeline_core.model_scheduler import ModelScheduler
    if not ModelScheduler.acquire_worker_lock(root):
        return {"processed": 0, "errors": [], "skipped": "worker_busy"}

    try:
        for f in sorted(qdir.iterdir()):
            if not f.is_file() or f.suffix.lower() != ".pdf":
                continue
            if f.name.endswith(".processing"):
                continue
            try:
                result = process_document(root, f)
                if result:
                    return {"processed": 1, "errors": []}
                return {"processed": 0, "errors": []}
            except Exception as e:
                logger.error(f"M2 失败: {f.name}: {e}")
                return {"processed": 0, "errors": [str(e)]}
    finally:
        ModelScheduler.release_worker_lock(root)

    return {"processed": 0, "errors": []}
