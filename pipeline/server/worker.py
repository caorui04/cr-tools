"""后台 watcher：驱动文件在管线各级队列间流动（M1→M2→M3→M4）。

设计依据《笔记本端管线调度详细设计》：单进程多 watcher。
每个 watcher = 轮询一个队列目录，有文件就调对应模块的调度入口。
与 FastAPI 服务同进程启动（serve() 时拉起守护线程）。

2026-08-01 增强（内存预算调度）：
- 启动时清理孤儿 M2 worker（防 1.7GB × N 叠加）
- M2 spawn 前互斥锁（dispatcher 内实现）
- M3 翻译模型按需拉起（02 队列非空 → ensure，清空保活超时 → 停）
- M4 嵌入模型常驻保活（死则尝试拉起）
- 周期巡检：孤儿清理 + 空闲模型停机
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from pipeline_core.config import PipelineConfig

logger = logging.getLogger(__name__)

# 孤儿巡检周期（秒）：清理反复出现的孤儿 worker
ORPHAN_REAP_INTERVAL_S = 30.0
# 调度器 tick 周期（秒）：空闲停机检查
SCHEDULER_TICK_S = 30.0


def _queue_has_files(queue_dir: Path) -> bool:
    if not queue_dir.exists():
        return False
    return any(
        f.is_file() and not f.name.endswith(".processing")
        for f in queue_dir.iterdir()
    )


def _watcher(name: str, queue_dir: Path, handler, interval: float):
    """通用 watcher：queue 有文件就执行 handler，否则睡眠。"""
    logger.info(f"watcher 启动: {name} (interval={interval}s)")

    def loop():
        while True:
            try:
                if _queue_has_files(queue_dir):
                    handler()
            except Exception as e:
                logger.error(f"watcher {name} 执行失败: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, name=f"watcher-{name}", daemon=True)
    t.start()
    return t


def _scheduler_loop(sched):
    """周期巡检：孤儿清理 + 空闲模型停机（内存预算防叠加）。"""
    while True:
        try:
            time.sleep(SCHEDULER_TICK_S)
            sched.reap_orphans()
            for sid in ("translate", "citation", "m2_worker"):
                sched.maybe_stop(sid)
        except Exception as e:
            logger.error(f"调度器巡检失败: {e}")


def start_watchers(root: Path, config: PipelineConfig):
    """启动 M1/M2/M3/M4 四个队列 watcher + 内存调度器。"""
    # 模型调度器（install_dir 解析：优先环境变量，回退本地模型目录）
    from pipeline_core.model_scheduler import ModelScheduler

    install_dir = _resolve_install_dir()
    sched = ModelScheduler(
        install_dir=install_dir,
        root=root,
        budget_mb=_cfg_budget(config),
        idle_stop_s=_cfg_idle_stop(config),
    )

    # 启动前清理孤儿 M2 worker（用户关 UI / 主进程被杀遗留）
    killed = sched.reap_orphans()
    if killed:
        logger.warning(f"调度器: 启动清理 {killed} 个孤儿 M2 worker")

    from m1_identifier.dispatcher import dispatch_all as m1_dispatch
    from m2_ocr.dispatcher import dispatch_all as m2_dispatch
    from m3_translator.dispatcher import dispatch_all as m3_dispatch
    from m4_vectordb.indexer import index_all_pending
    from m4_vectordb.embedder import GGUFEmbedder

    embedder = GGUFEmbedder()
    _embedder_warned = {"flag": False}

    # M3 翻译 provider（Hy-MT2 8081）：由调度器按需拉起，不常驻
    from llm.gguf_provider import GgufProvider
    m3_provider = GgufProvider(
        model_name="hy-mt2",
        port=8081,
        ctx_size=2048,
    )

    def m4_handler():
        # 嵌入模型常驻保活：死则尝试拉起（预算内）
        if not embedder.check_alive():
            if not _embedder_warned["flag"]:
                logger.warning(
                    "M4 watcher: embedding 服务 (8083) 未在线，尝试拉起 "
                    "（若预算不足会拒绝，文档留队列）"
                )
            sched.ensure("embedding")
            _embedder_warned["flag"] = True
            return
        _embedder_warned["flag"] = False
        index_all_pending(root, embedder)

    def m3_handler():
        # 翻译模型按需：02 队列非空 → 拉起 → 翻译
        sched.ensure("translate")
        m3_dispatch(root, m3_provider)

    start_kwargs = {"root": root, "config": config}
    _watcher("M1", root / "00_待处理", lambda: m1_dispatch(**start_kwargs), interval=2.0)
    _watcher("M2", root / "01_OCR队列", lambda: m2_dispatch(root), interval=5.0)
    _watcher("M3", root / "02_翻译队列", m3_handler, interval=5.0)
    _watcher("M4", root / "03_知识库原文", m4_handler, interval=5.0)

    # 调度器周期巡检（孤儿清理 + 空闲停机）
    t = threading.Thread(target=_scheduler_loop, args=(sched,), name="scheduler", daemon=True)
    t.start()

    logger.info("全部 watcher 已启动 (M1/M2/M3/M4) + 调度器 (budget=%dMB, idle=%ds)",
                _cfg_budget(config), _cfg_idle_stop(config))
    return sched


def _resolve_install_dir() -> Path:
    """解析模型安装目录（自包含，cr-tools M1 切片 2 修订）。

    优先 KB_TOOLS_DIR 环境变量（便携包/自定义布局覆盖）；
    默认 = <项目根>/tools（本文件 pipeline/server/worker.py 上溯两级到项目根），
    结构约定：tools/llama/llama-server.exe + tools/models/*.gguf（ModelScheduler 注册表）。
    不再依赖 %LOCALAPPDATA%/pi-mono-desktop（老库安装目录，已废弃）。
    """
    env_dir = os.environ.get("KB_TOOLS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parents[2] / "tools"


def _cfg_budget(config: PipelineConfig) -> int:
    """内存预算（MB）：config.models.scheduler.memory_budget_mb，默认 8000。"""
    return int(config.models.scheduler.get("memory_budget_mb", 8000))


def _cfg_idle_stop(config: PipelineConfig) -> float:
    """空闲保活（秒）：config.models.scheduler.idle_stop_s，默认 600。"""
    return float(config.models.scheduler.get("idle_stop_s", 600.0))
