"""pandoc 转换封装（S7，批次 P；依 U1 权威配置 + U2 proc.py）。

routes.py 两处 pandoc 调用（docx→md 草稿导入 / md→docx 阶段交付件）同一模式：
取 exe → proc.run 执行 → 失败由调用方按自身语义降级。本模块收敛为：
- ``pandoc_exe()``：exe 路径唯一取径（U1：config.yaml ``pandoc_path``，缺省回退
  PATH 中的 ``pandoc``），调用方禁止再各自读配置；
- ``pandoc_convert()``：经 proc.run（U2：argv 数组/超时归一/脱敏日志），
  exe 缺失等执行层异常归一为 ``ok=False`` 结果（不抛 FileNotFoundError），
  错误返回形态统一，调用方只判 ``ok`` 与目标文件是否生成。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import load_config
from .proc import ProcResult, run

logger = logging.getLogger(__name__)

# 两处既有调用点一致的超时（pandoc 转换为常态秒级操作，60s 兜底）
DEFAULT_TIMEOUT = 60


def pandoc_exe() -> str:
    """pandoc 可执行文件路径唯一取径：config.yaml pandoc_path，缺省 "pandoc"（走 PATH）。"""
    cfg = load_config()
    return getattr(cfg, "pandoc_path", "") or "pandoc"


def pandoc_convert(
    src: str | Path,
    dst: str | Path,
    extra_args: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> ProcResult:
    """pandoc src → dst（经 proc.run，统一错误返回）。

    Args:
        src: 源文件路径。
        dst: 目标文件路径（经 ``-o`` 传入）。
        extra_args: 额外 pandoc 参数（置于 src 与 -o 之间）。
        timeout: 超时秒数（超时归一为 timed_out=True，不抛）。

    Returns:
        ProcResult。``ok=True`` 仅表示进程正常退出，目标文件是否生成由调用方判
        （pandoc 偶发 rc=0 但未产出）。exe 缺失/执行层异常归一为
        ``ok=False, returncode=-1, stderr=说明``，不抛 OSError。
    """
    argv = [pandoc_exe(), str(src)]
    if extra_args:
        argv += list(extra_args)
    argv += ["-o", str(dst)]
    try:
        return run(argv, timeout=timeout)
    except OSError as e:
        # exe 缺失（FileNotFoundError）等执行层异常：归一为失败结果，调用方优雅降级
        logger.warning(f"pandoc 不可执行（{argv[0]}）: {e}")
        return ProcResult(
            ok=False, returncode=-1, stdout="",
            stderr=f"pandoc 不可用: {e}", timed_out=False,
        )
