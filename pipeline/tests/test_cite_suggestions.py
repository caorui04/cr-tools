"""T4 疑似引用建议面板端点测试（第十条定案，2026-08-12 用户定案判据）。

判据：保存备注 = 增量部分提炼摘要（AI 生成）；T4 扫描只需根据保存备注检索即可——
备注检索 top1 命中（score ≥ 0.15，实测真命中 ≥0.24 / 无关 ≤0.03）即建议该文档。

覆盖：保存触发（假 provider 生成摘要备注）→ 建议 1 条、低分不命中、备注过短
（无增量/AI 降级）不扫、dismiss 移除、重复保存无增量不重复打扰。

扫描依赖向量检索、嵌入与 LLM provider——monkeypatch 三处（运行时 from-import，
patch 生效），黑盒经端点验证。
"""

import time

import pytest
from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from server import workbench_state as ws
from server.routes import create_app

PAPER = "p_t400"

ADDED_PARA = "1949年以前是近代华北社会经济史研究的起步阶段。海内外学者针对华北经济社会开展实地调查研究，留下大量一手资料，代表性著作有李景汉的北平郊外之乡村家庭与定县社会概况调查等。"

# 基准内容：首存建 v1（has_history=False 不触发后台）；第二次保存才有增量基准
BASE = "初始内容仅供版本基准使用，本次先保存一版。"


class FakeEmbedder:
    """假嵌入器：check_alive True（扫描的存活检查通过）。"""

    def check_alive(self):
        return True


class FakeProvider:
    """假 LLM provider：_chat 返回增量摘要备注（T4 扫描源）。"""

    def _chat(self, sys, user):
        return "新增华北社会经济史研究综述段落"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    d = tmp_path / "papers" / PAPER
    d.mkdir(parents=True, exist_ok=True)
    (d / "drafts").mkdir()
    (d / "drafts" / "d1.md").write_text("初始内容", encoding="utf-8")
    ws.ensure(d)

    def _make_fake_search(hit):
        def fake_search(root, embedder, query, top_k, doc_hash=None):
            return [hit] if hit else []
        return fake_search

    monkeypatch.setattr("m4_vectordb.embedder.Embedder", FakeEmbedder)
    monkeypatch.setattr("llm.providers.active_provider", lambda: FakeProvider())
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    return {"client": client, "headers": headers, "make_search": _make_fake_search}


def _mk_hit(doc_hash="abc123", score=0.3, text=ADDED_PARA):
    """假 top1 命中：score 0.3 为真实形态（备注 vs 库内文档 ≥0.24），text 供建议片段。"""
    return {"doc_hash": doc_hash, "score": score, "text": text,
            "doc_meta": {"source_file": "海外中国史研究文献综述.docx", "title": None}}


def _save(ctx, content, note=""):
    r = ctx["client"].post(
        f"/papers/{PAPER}/drafts/d1.md",
        headers=ctx["headers"],
        json={"content": content, "note": note, "action": "edit"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _save_trigger(ctx, content):
    """首存建 v1（不触发后台），再保存增量内容触发备注+扫描（自动备注路径）。"""
    _save(ctx, BASE)
    return _save(ctx, content)


def _await_suggestions_file(ctx, tmp_path, timeout=10.0):
    """等待后台线程写完（备注生成 ≤15s 内层超时 + 扫描）。"""
    sugg_path = tmp_path / "papers" / PAPER / "citesuggestions.json"
    deadline = time.time() + timeout
    while time.time() < deadline and not sugg_path.exists():
        time.sleep(0.2)
    assert sugg_path.exists(), "后台线程未在超时内写出建议文件"
    return sugg_path


def _get_items(ctx):
    return ctx["client"].get(f"/papers/{PAPER}/citesuggestions", headers=ctx["headers"]).json()["items"]


def test_save_triggers_scan_by_note(ctx, tmp_path, monkeypatch):
    # 假 search 命中：备注检索 → 建议 1 条（doc/score/text=备注）
    monkeypatch.setattr("m4_vectordb.search.search", ctx["make_search"](_mk_hit()))
    _save_trigger(ctx, ADDED_PARA)
    _await_suggestions_file(ctx, tmp_path)
    items = _get_items(ctx)
    assert len(items) == 1
    it = items[0]
    assert it["doc_id"] == "abc123"
    assert it["score"] == 0.3
    assert "华北社会经济史" in it["text"]  # 建议片段 = 增量摘要备注
    assert it["anchor"]
    # 备注已写回时间线（增量摘要）
    v = ctx["client"].get(f"/papers/{PAPER}/versions", headers=ctx["headers"]).json()["versions"]
    assert any("华北社会经济史" in (e.get("note") or "") for e in v)


def test_low_score_no_suggestion(ctx, tmp_path, monkeypatch):
    # 备注检索 top1 score 0.03（无关）→ 无建议（<0.15）
    monkeypatch.setattr("m4_vectordb.search.search", ctx["make_search"](_mk_hit(score=0.03)))
    _save_trigger(ctx, ADDED_PARA)
    _await_suggestions_file(ctx, tmp_path)
    assert _get_items(ctx) == []


def test_no_provider_skips_scan(ctx, tmp_path, monkeypatch):
    # provider 不可用（假 provider 无 _chat）→ 备注生成降级 → 扫描跳过写空（不打扰）
    class StubProvider:
        pass

    monkeypatch.setattr("llm.providers.active_provider", lambda: StubProvider())
    monkeypatch.setattr("m4_vectordb.search.search", ctx["make_search"](_mk_hit()))
    _save_trigger(ctx, ADDED_PARA)
    _await_suggestions_file(ctx, tmp_path)
    assert _get_items(ctx) == []


def test_dismiss_removes_item(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr("m4_vectordb.search.search", ctx["make_search"](_mk_hit()))
    _save_trigger(ctx, ADDED_PARA)
    _await_suggestions_file(ctx, tmp_path)
    items = _get_items(ctx)
    assert len(items) == 1
    idx = items[0]["idx"]
    r = ctx["client"].post(
        f"/papers/{PAPER}/citesuggestions/dismiss", headers=ctx["headers"], json={"idx": idx}
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert _get_items(ctx) == []


def test_unchanged_content_no_suggestion(ctx, tmp_path, monkeypatch):
    # 无增量：首次触发保存出建议（增量），再保存相同内容 → 增量空 → 扫描写空（不重复打扰）
    monkeypatch.setattr("m4_vectordb.search.search", ctx["make_search"](_mk_hit()))
    _save_trigger(ctx, ADDED_PARA)
    _await_suggestions_file(ctx, tmp_path)
    assert len(_get_items(ctx)) == 1
    _save(ctx, ADDED_PARA)
    time.sleep(1.5)
    assert _get_items(ctx) == []
