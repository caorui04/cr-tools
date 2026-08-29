"""pandoc.py 单测（S7，批次 P）。

覆盖：
(a) exe 唯一取径：config pandoc_path 优先，空值回退 "pandoc"；
(b) argv 构造：src/dst/-o 顺序 + extra_args 位置 + timeout 透传 proc.run；
(c) exe 缺失（FileNotFoundError/OSError）归一为 ok=False 结果，不抛异常——
    import_docx 因此走 {"ok": False, "reason": ...} 优雅失败，而非 500。
"""

import pipeline_core.pandoc as pandoc_mod
from pipeline_core.pandoc import pandoc_convert, pandoc_exe
from pipeline_core.proc import ProcResult


def _fake_cfg(pandoc_path: str):
    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.pandoc_path = pandoc_path
    return cfg


def test_exe_from_config(monkeypatch):
    """(a) config.yaml pandoc_path 为权威取径。"""
    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _fake_cfg("C:/tools/pandoc.exe"))
    assert pandoc_exe() == "C:/tools/pandoc.exe"


def test_exe_fallback_default(monkeypatch):
    """(a 补) pandoc_path 为空/缺省时回退 PATH 中的 pandoc。"""
    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _fake_cfg(""))
    assert pandoc_exe() == "pandoc"

    class _NoAttr:
        pass

    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _NoAttr())
    assert pandoc_exe() == "pandoc"


def test_convert_argv_construction(monkeypatch):
    """(b) argv = [exe, src, *extra_args, -o, dst]，timeout 透传。"""
    captured = {}

    def fake_run(argv, timeout=60, redact=None):
        captured["argv"] = argv
        captured["timeout"] = timeout
        return ProcResult(ok=True, returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _fake_cfg("pandocX"))
    monkeypatch.setattr(pandoc_mod, "run", fake_run)

    r = pandoc_convert("a.docx", "b.md", timeout=30)
    assert r.ok
    assert captured["argv"] == ["pandocX", "a.docx", "-o", "b.md"]
    assert captured["timeout"] == 30

    pandoc_convert("a.md", "b.docx", extra_args=["--standalone"])
    assert captured["argv"] == ["pandocX", "a.md", "--standalone", "-o", "b.docx"]
    assert captured["timeout"] == 60  # 缺省 60s（与两处原调用点一致）


def test_convert_exe_missing_graceful(monkeypatch):
    """(c) exe 缺失：proc.run 抛 FileNotFoundError → 归一 ok=False 结果，不上抛。"""
    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _fake_cfg("nonexistent-pandoc-xyz.exe"))
    r = pandoc_convert("a.docx", "b.md")
    assert r.ok is False
    assert r.returncode == -1
    assert r.timed_out is False
    assert "pandoc" in r.stderr


def test_convert_oserror_normalized(monkeypatch):
    """(c 补) run 层抛 OSError（如权限拒绝）同样归一为失败结果。"""

    def fake_run(argv, timeout=60, redact=None):
        raise OSError("permission denied")

    monkeypatch.setattr(pandoc_mod, "load_config", lambda: _fake_cfg("pandoc"))
    monkeypatch.setattr(pandoc_mod, "run", fake_run)
    r = pandoc_convert("a.docx", "b.md")
    assert r.ok is False
    assert "permission denied" in r.stderr
