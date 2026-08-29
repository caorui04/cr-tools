"""FastAPI 入口。"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

from pipeline_core.config import load_config
from pipeline_core.filesystem import init_pipeline_dirs
from pipeline_core.logging_setup import setup_logging

from server.routes import create_app
from server.worker import start_watchers


def serve(config_path: Path | None = None):
    """启动 FastAPI 服务 + 管线 watcher。"""
    config = load_config(config_path)
    init_pipeline_dirs(config.pipeline_root)
    setup_logging(config.pipeline_root, "server")

    # 启动队列 watcher（M1→M2→M3→M4 文件流驱动）
    start_watchers(config.pipeline_root, config)

    app = create_app(config)
    uvicorn.run(app, host="127.0.0.1", port=config.http_port)


def ocr_worker_entry(argv: list[str]) -> int:
    """M2 OCR 子进程入口（父进程经 `sys.executable --ocr-worker ...` 自调用）。

    参数: --ocr-worker <root> <doc_id> <pdf_path>
    """
    from m2_ocr.ocr_worker import main as ocr_worker_main

    return ocr_worker_main(argv)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--ocr-worker":
        sys.exit(ocr_worker_entry(sys.argv[2:]))
    serve()
