"""proc.py 单测（U2 命令构造与转义规范第 4 节验收用例，批次 F）。

覆盖契约验收四点，全部用 sys.executable -c 自举，不依赖外部命令：
(a) 含空格参数不经 shell 拆分（argv 数组形式原样传递）；
(b) 超时归一（sleep 超 timeout → timed_out=True 不抛异常）；
(c) 日志脱敏（redact 值不出现在日志/展示串中）；
(d) 正常命令 ok=True + stderr 尾 500 字截断。
"""

import logging
import sys

from pipeline_core.proc import STDERR_TAIL, ProcResult, _display_argv, run


def test_arg_with_spaces_not_split():
    """(a) "a b.txt" 作为单个 argv 元素原样到达子进程，不经 shell 拆成两个。"""
    r = run([sys.executable, "-c", "import sys; print(len(sys.argv) - 1); print(sys.argv[1])", "a b.txt"])
    assert r.ok
    lines = r.stdout.strip().splitlines()
    assert lines[0] == "1"  # 子进程只收到 1 个参数
    assert lines[1] == "a b.txt"  # 内容原样


def test_timeout_normalized():
    """(b) sleep 5s + timeout=1 → timed_out=True、ok=False，不抛 TimeoutExpired。"""
    r = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert isinstance(r, ProcResult)
    assert r.timed_out is True
    assert r.ok is False
    assert r.returncode == -1


def test_redact_value_not_in_log(caplog):
    """(c) redact 指定的秘密值不出现在日志记录中（替换为 ***）。"""
    secret = "sk-test-SECRET-12345"
    with caplog.at_level(logging.INFO, logger="pipeline_core.proc"):
        r = run(
            [sys.executable, "-c", "pass", f"--token={secret}"],
            redact=[secret],
        )
    assert r.ok
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret not in log_text
    assert "***" in log_text


def test_display_argv_redacts_substring():
    """(c 补) 展示串层面：秘密值嵌在参数里也被替换，其余内容原样。"""
    shown = _display_argv(["pandoc", "in.txt", "--api_key=abc123"], ["abc123"])
    assert shown == "pandoc in.txt --api_key=***"
    # 空串秘密不打散展示串
    assert _display_argv(["a", "b"], [""]) == "a b"


def test_normal_command_ok():
    """(d) 正常命令：ok=True、returncode=0、stdout 带内容。"""
    r = run([sys.executable, "-c", "print('hello')"])
    assert r.ok is True
    assert r.returncode == 0
    assert r.timed_out is False
    assert r.stdout.strip() == "hello"


def test_stderr_tail_truncated():
    """(d 补) stderr 只留尾 500 字：超长输出截断为尾部窗口。"""
    r = run([sys.executable, "-c", f"import sys; sys.stderr.write('x' * {STDERR_TAIL * 4})"])
    assert r.ok  # 仅写 stderr 不影响退出码
    assert len(r.stderr) == STDERR_TAIL
    assert set(r.stderr) == {"x"}
