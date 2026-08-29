"""M2 dispatcher 页级超时容错（2026-08-17 会话 11 方案 1）。

背景：a6fff GB/T 页 9（1031 表格线矢量复杂版面）在 CPU 热降频下推理时长
可从 88s 膨胀到 >20min（接近死循环），原 600s 心跳必然误杀 → 断点续传+重试
死循环 → 3 次入 99_异常。方案 1：页级超时（30min）→ 同一页连续 2 次超时
→ 占位跳过（partial 断点恢复视为已完成），文档继续流转。

被测缝：_handle_page_timeout（跳过决策）+ _skip_page（占位写入）
        + _watch_worker（页级计时超时判定）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from m2_ocr import dispatcher


@pytest.fixture(autouse=True)
def _reset_timeout_state():
    """每个用例前重置页超时连续计数（模块级状态）。"""
    dispatcher._page_timeout_state = {"page": None, "count": 0}
    yield


def test_skip_page_appends_placeholder(tmp_path: Path) -> None:
    """跳过页：向 partial 追加占位，格式与断点恢复正则兼容。"""
    dispatcher._skip_page(tmp_path, "doc1", 9)
    partial = tmp_path / "progress" / "doc1.partial.ocr.md"
    content = partial.read_text(encoding="utf-8")
    assert "<!-- page 9 -->" in content
    assert "OCR_SKIPPED" in content


def test_page_timeout_first_try_no_skip(tmp_path: Path) -> None:
    """页超时第 1 次：不跳过，state 计数=1，partial 无占位。"""
    dispatcher._handle_page_timeout(tmp_path, "doc1", 9)
    partial = tmp_path / "progress" / "doc1.partial.ocr.md"
    assert not partial.exists()
    assert dispatcher._page_timeout_state == {"page": 9, "count": 1}


def test_page_timeout_second_try_skips_and_resets(tmp_path: Path) -> None:
    """同一页连续 2 次超时：跳过（partial 写占位），state 重置。"""
    dispatcher._handle_page_timeout(tmp_path, "doc1", 9)
    dispatcher._handle_page_timeout(tmp_path, "doc1", 9)
    partial = tmp_path / "progress" / "doc1.partial.ocr.md"
    content = partial.read_text(encoding="utf-8")
    assert "<!-- page 9 -->" in content and "OCR_SKIPPED" in content
    assert dispatcher._page_timeout_state == {"page": None, "count": 0}


def test_page_timeout_diff_page_resets_counter(tmp_path: Path) -> None:
    """不同页超时：计数重置为新页，不误跳。"""
    dispatcher._handle_page_timeout(tmp_path, "doc1", 9)
    dispatcher._handle_page_timeout(tmp_path, "doc1", 10)
    assert dispatcher._page_timeout_state == {"page": 10, "count": 1}
    partial = tmp_path / "progress" / "doc1.partial.ocr.md"
    assert not partial.exists()


def test_page_timeout_none_page_noop(tmp_path: Path) -> None:
    """timeout_page=None（异常路径）：无操作不崩溃。"""
    dispatcher._handle_page_timeout(tmp_path, "doc1", None)
    assert dispatcher._page_timeout_state == {"page": None, "count": 0}


class _FakeProc:
    """模拟 worker 进程：stdout 可迭代 + poll 恒 None（进程活着）。"""

    def __init__(self, lines: list[str], release: threading.Event):
        self._lines = lines
        self._release = release

    @property
    def stdout(self):
        def gen():
            for ln in self._lines:
                yield ln
            # 阻塞直到释放：模拟卡死（无更多输出但进程存活）
            self._release.wait(30)
            yield ""

        return gen()

    def poll(self):
        return None


def test_watch_worker_page_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """无页进展超过阈值 → page_timeout（含卡住页号）。"""
    monkeypatch.setattr(dispatcher, "PAGETIMEOUT_S", 0.5)
    release = threading.Event()
    fake = _FakeProc(['{"type": "progress", "page": 8, "total": 21}\n'], release)
    status, md, err, page = dispatcher._watch_worker(fake)
    assert status == "page_timeout"
    assert page == 8
    assert "超时" in (err or "")


def test_watch_worker_noise_does_not_reset_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """非协议噪音（ReduceMean）不刷新页计时：噪音后仍会 page_timeout。"""
    monkeypatch.setattr(dispatcher, "PAGETIMEOUT_S", 0.5)
    release = threading.Event()
    # 一条 progress 后只有噪音，无新 progress → 仍应超时
    fake = _FakeProc(
        [
            '{"type": "progress", "page": 8, "total": 21}\n',
            "ReduceMeanCheckIfOneDNNSupport\n" * 50,
        ],
        release,
    )
    status, md, err, page = dispatcher._watch_worker(fake)
    assert status == "page_timeout"
    assert page == 8


def test_watch_worker_done_path() -> None:
    """正常 done：返回 done + md 名（回归）。"""
    release = threading.Event()
    fake = _FakeProc(
        [
            '{"type": "progress", "page": 1, "total": 1}\n',
            '{"type": "done", "pages": 1, "md": "x.ocr.md"}\n',
        ],
        release,
    )
    status, md, err, page = dispatcher._watch_worker(fake)
    assert status == "done"
    assert md == "x.ocr.md"
    assert page is None
