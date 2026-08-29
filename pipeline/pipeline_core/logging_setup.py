"""日志系统。

为每个模块创建独立 log 文件 <root>/logs/<module_name>.log。
同时输出 stderr（开发便利）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_registry: dict[str, logging.Logger] = {}


def setup_logging(
    root: Path, module_name: str, level: str = "INFO"
) -> logging.Logger:
    """为指定模块创建独立 log 文件并配置 stderr 输出。

    Args:
        root: pipeline_root
        module_name: 模块名（用于文件名和 logger 名）
        level: 日志级别

    Returns:
        配置好的 Logger。
    """
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(module_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # 文件 handler
    file_handler = logging.FileHandler(
        logs_dir / f"{module_name}.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # stderr handler
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _registry[module_name] = logger
    return logger


def get_logger(module_name: str) -> logging.Logger:
    """获取已配置的 logger（调用前需 setup_logging）。"""
    if module_name in _registry:
        return _registry[module_name]
    return logging.getLogger(module_name)
