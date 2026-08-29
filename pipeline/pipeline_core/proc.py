"""子进程统一执行封装（U2 命令构造与转义规范 3.2）。

统一"执行 + 超时 + 错误归一 + 日志"四件事，替代各调用点手写 subprocess.run：
- argv 一律 list 形式（shell=False），禁字符串拼接命令（契约第 1 节禁令固化）；
- TimeoutExpired 归一为 timed_out=True 不抛——超时是常态工况（pandoc/模型），
  不应以异常形式穿透调用方；
- stderr 只留尾 500 字进结果：尾部才是报错正文，全量留存会撑爆日志与内存；
- 单行日志 [proc] → argv 展示 + 耗时 + rc；redact 指定的秘密参数值替换为 ***
  （契约第 1.3 条：命令行进日志前必须脱敏）。

设计边界：只归一超时，其余异常（如 FileNotFoundError = 可执行文件缺失）
原样抛出，由调用方按自身语义降级——保持迁移点对外行为等价（契约 3.2 迁移要求）。
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# stderr 截断长度：只留尾部，报错正文通常在末尾
STDERR_TAIL = 500


@dataclass
class ProcResult:
    """子进程执行结果。ok = 正常结束且 returncode == 0。"""

    ok: bool
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def _display_argv(argv: list[str], redact: list[str] | None) -> str:
    """argv 拼接为展示串（仅用于日志），redact 列出的秘密值出现处替换为 ***。

    用子串替换而非整参匹配：秘密值可能嵌在 `--key=VALUE` 这类单参数里。
    空串秘密跳过（否则 replace("", "***") 会把整串打散）。
    """
    secrets = [s for s in (redact or []) if s]
    parts = []
    for a in argv:
        shown = a
        for s in secrets:
            shown = shown.replace(s, "***")
        parts.append(shown)
    return " ".join(parts)


def run(
    argv: list[str],
    timeout: int = 60,
    redact: list[str] | None = None,
) -> ProcResult:
    """执行 argv（shell=False），统一超时/错误归一/日志。

    Args:
        argv: 命令参数数组（禁止 shell 字符串形式）。
        timeout: 超时秒数；超时归一为 timed_out=True，不抛 TimeoutExpired。
        redact: 秘密参数值列表，日志展示串中出现处替换为 ***（不影响真实执行）。

    Returns:
        ProcResult；超时 returncode 记 -1（无真实退出码可记）。

    Raises:
        FileNotFoundError 等执行层异常原样上抛，由调用方决定降级策略。
    """
    shown = _display_argv(argv, redact)
    t0 = time.monotonic()
    try:
        cp = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - t0
        # 超时被 kill 的进程也可能已吐出部分输出，尽量带回供诊断
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        err = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")
        logger.warning(f"[proc] → {shown} | {elapsed:.2f}s | rc=-1 (timed_out)")
        return ProcResult(ok=False, returncode=-1, stdout=out, stderr=err[-STDERR_TAIL:], timed_out=True)
    elapsed = time.monotonic() - t0
    logger.info(f"[proc] → {shown} | {elapsed:.2f}s | rc={cp.returncode}")
    return ProcResult(
        ok=cp.returncode == 0,
        returncode=cp.returncode,
        stdout=cp.stdout or "",
        stderr=(cp.stderr or "")[-STDERR_TAIL:],
        timed_out=False,
    )
