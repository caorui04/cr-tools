"""atomic_write 并发回归（2026-08-10 paper_detail 500 竞态）。

场景：uvicorn 线程池下多线程并发 atomic_write 同一路径 + 另一线程持续
Python open() 读（无 FILE_SHARE_DELETE 共享）——修复前 os.replace 撞读者
句柄即 WinError 5；修复后 PermissionError 退避重试 + 写锁串行化。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from pipeline_core.atomic import atomic_write


def _reader_loop(path: Path, stop: threading.Event) -> None:
    """以请求级节奏读（模拟并发请求线程的 read_text）。

    注意：reader 必须留窗口——紧贴的零间隔读循环会让文件句柄几乎恒开，
    任何重试策略都无法保证落入窗口；生产读者是请求级的偶发读。
    全覆盖的进程内读写已由 workbench_state 的模块锁串行化（见其并发测试），
    本用例验证 atomic_write 退避重试能吸收**偶发**的外部读者冲突。
    """
    while not stop.is_set():
        try:
            path.read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass  # 替换瞬间的瞬态失败无所谓，只要不崩
        time.sleep(0.005)


def test_atomic_write_concurrent_writers_and_reader(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write(target, "{}")

    stop = threading.Event()
    readers = [threading.Thread(target=_reader_loop, args=(target, stop), daemon=True) for _ in range(3)]
    for r in readers:
        r.start()

    errors: list[Exception] = []

    def writer(i: int) -> None:
        try:
            for j in range(15):
                atomic_write(target, json.dumps({"w": i, "j": j}))
        except Exception as e:  # noqa: BLE001 - 测试要捕获一切汇报
            errors.append(e)

    writers = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for w in writers:
        w.start()
    for w in writers:
        w.join(timeout=30)
    stop.set()
    for r in readers:
        r.join(timeout=5)

    assert not errors, f"并发写出错: {errors[:3]}"
    # 最终文件必须是某个完整 JSON（无半截文件）
    final = json.loads(target.read_text(encoding="utf-8"))
    assert set(final) == {"w", "j"}


def test_atomic_write_permission_error_retry_exhausted(tmp_path: Path, monkeypatch) -> None:
    """持续占用目标（模拟顽固锁）→ 重试耗尽后仍抛 PermissionError，且 tmp 被清理。"""
    import os

    import pipeline_core.atomic as atomic_mod

    target = tmp_path / "locked.json"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setattr(atomic_mod.time, "sleep", lambda _: None)  # 测试不等真实退避

    real_replace = os.replace

    def always_denied(src, dst):
        raise PermissionError(5, "拒绝访问", str(dst))

    monkeypatch.setattr(atomic_mod.os, "replace", always_denied)
    try:
        try:
            atomic_write(target, "y")
            raise AssertionError("应抛 PermissionError")
        except PermissionError:
            pass
    finally:
        monkeypatch.setattr(atomic_mod.os, "replace", real_replace)
    # tmp 已清理，目录里只剩原文件
    assert [p.name for p in tmp_path.iterdir()] == ["locked.json"]
