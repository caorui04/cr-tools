"""速览卡一等数据 + OA 入库合并预生成测试（2026-08-13 用户拍板）。

覆盖：digests/ 权威目录读写、旧 sidecar 缓存位懒迁移、删除联动、
端点读新存储、oa_ingest 成功后后台预生成（共用同一份提取文本）、
stub/不可用后端跳过预生成。

不打真网：provider/make_digest/download_file 均 monkeypatch。
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from pipeline_core.http_client import NetResult
from server.routes import (
    _digest_paths,
    _digest_read,
    _digest_remove,
    _digest_write,
    create_app,
)


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}, tmp_path


# ---- 一等数据存储 ----


def test_digest_write_read_authoritative(tmp_path):
    """写入落 digests/ 权威位；读取命中；旧位副本清除。"""
    new, old = _digest_paths(tmp_path, "d1")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}", encoding="utf-8")  # 旧位有残留副本
    _digest_write(tmp_path, "d1", {"title": "T", "sections": []})
    assert new.exists() and not old.exists()
    d = _digest_read(tmp_path, "d1")
    assert d["title"] == "T"


def test_digest_lazy_migration(tmp_path):
    """旧缓存位命中 → 返回数据 + 迁移到权威位（移动不复制）。"""
    new, old = _digest_paths(tmp_path, "d2")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"title": "旧卡", "sections": [1]}), encoding="utf-8")
    d = _digest_read(tmp_path, "d2")
    assert d["title"] == "旧卡"
    assert new.exists() and not old.exists()  # 已迁移
    # 再次读取走权威位
    assert _digest_read(tmp_path, "d2")["title"] == "旧卡"


def test_digest_remove_both(tmp_path):
    new, old = _digest_paths(tmp_path, "d3")
    new.parent.mkdir(parents=True, exist_ok=True)
    old.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("{}", encoding="utf-8")
    old.write_text("{}", encoding="utf-8")
    _digest_remove(tmp_path, "d3")
    assert not new.exists() and not old.exists()


def test_digest_endpoint_reads_store(client):
    """端点经新存储返回 cached（一等数据优先，不重生成）。"""
    c, h, root = client
    _digest_write(root, "d9", {"title": "卡", "sections": [{"heading": "s", "points": []}], "doc_id": "d9"})
    r = c.get("/docs/d9/digest", headers=h)
    d = r.json()
    assert d.get("source") == "cached" and d["title"] == "卡"
    # progress 端点同样认新存储
    p = c.get("/docs/d9/digest/progress", headers=h).json()
    assert p["status"] == "done"


# ---- OA 入库合并速览卡预生成 ----

_REQ = {
    "url": "https://oa.example.com/p.pdf",
    "title": "测试文献",
    "source": "arXiv",
    "search_date": "2026-08-13",
}


def _fake_download(tmp_path):
    def fake(url, dest, **k):
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "digest auto generation test content " * 10)
        doc.save(str(dest))
        doc.close()
        return NetResult(ok=True, status=200, data=dest.stat().st_size, error="", elapsed=0.1)
    return fake


class _FakeProvider:
    name = "cloud-fake"


def test_oa_ingest_auto_digest(client, tmp_path, monkeypatch):
    """OA 入库成功 → 后台预生成速览卡（同一份提取文本）→ digests/ 落卡。"""
    c, h, root = client
    monkeypatch.setattr("pipeline_core.http_client.download_file", _fake_download(tmp_path))
    monkeypatch.setattr("llm.providers.active_provider", lambda: _FakeProvider())
    monkeypatch.setattr(
        "m6_digest.digest_skill.make_digest",
        lambda text, provider, on_progress=None: {"title": "自动卡", "sections": [], "chars": len(text)},
    )
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    data = r.json()
    assert data["ok"] and data["auto_digest"] is True
    assert "速览卡后台生成中" in data["message"]
    # 后台线程落卡（等至多 5s）
    new, _ = _digest_paths(root, data["doc_id"])
    for _ in range(50):
        if new.exists():
            break
        time.sleep(0.1)
    assert new.exists()
    card = json.loads(new.read_text(encoding="utf-8"))
    assert card["title"] == "自动卡" and card["chars"] > 100  # 同一份提取文本传入


def test_oa_ingest_auto_digest_skipped_when_stub(client, tmp_path, monkeypatch):
    """后端 stub（本地模型未运行/完全本地）→ 跳过预生成，入库不受影响。"""
    c, h, root = client
    monkeypatch.setattr("pipeline_core.http_client.download_file", _fake_download(tmp_path))

    class _Stub:
        name = "stub"

    monkeypatch.setattr("llm.providers.active_provider", lambda: _Stub())

    def _boom(*a, **k):
        raise AssertionError("stub 后端不得启动速览卡生成")

    monkeypatch.setattr("m6_digest.digest_skill.make_digest", _boom)
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    data = r.json()
    assert data["ok"] and data["auto_digest"] is False
    new, _ = _digest_paths(root, data["doc_id"])
    time.sleep(0.3)
    assert not new.exists()
