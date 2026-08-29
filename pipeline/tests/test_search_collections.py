"""Q2/Q3/Q4 走查驱动增强测试（2026-08-13 用户定案）。

Q2：/docs 增强——title 回退 bibliography.title、origin（oa_download|upload）、cited（项目清单已登记）。
Q3：收藏清单——创建（名带关键词/重名加序号）/列表/详情/删除（回收站）/导出 CSV（UTF-8 BOM）。
Q4：本地检索双 query 联合——中文 query 经翻译链得英译词，两路检索按 chunk 合并取高分；
    完全本地/翻译不可用 → 单路原词；翻译会话缓存生效。

全部 monkeypatch（嵌入/翻译/http），不打真网。
"""

import json

import pytest
from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from server.routes import create_app


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}, tmp_path


def _write_sidecar(root, doc_id, source_file, bib=None):
    sd = root / "sidecar"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / f"{doc_id}.json").write_text(
        json.dumps({
            "doc_id": doc_id, "source_file": source_file, "ingested_at": "2026-08-13T00:00:00",
            "page_count": 1, "bibliography": bib,
        }, ensure_ascii=False),
        encoding="utf-8",
    )


# ---- Q2：/docs 增强 ----


def test_docs_origin_cited_title_fallback(client):
    c, h, root = client
    _write_sidecar(root, "aaa111", "用户上传文献.pdf")  # 无 bibliography → upload + 无 title 回退
    _write_sidecar(root, "bbb222", "oa_下载.pdf", bib={"title": "OA 文献标题", "origin": "oa_download"})
    # bbb222 被 demo 项目清单登记
    pd = root / "papers" / "demo"
    pd.mkdir(parents=True)
    (pd / "bibliography.csl.json").write_text(
        json.dumps({"paper_id": "demo", "format": "GB/T 7714-2015",
                    "items": [{"seq": 1, "doc_id": "bbb222", "bibliography": {}}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    r = c.get("/docs", headers=h)
    docs = {d["doc_id"]: d for d in r.json()}
    assert docs["aaa111"]["origin"] == "upload" and docs["aaa111"]["cited"] is False
    assert docs["bbb222"]["origin"] == "oa_download" and docs["bbb222"]["cited"] is True
    assert docs["bbb222"]["title"] == "OA 文献标题"  # 顶层 None → 回退 bibliography.title
    assert docs["aaa111"]["title"] is None


# ---- Q3：收藏清单 ----

_ITEMS = [
    {"title": "文献甲", "authors": ["张三"], "journal": "期刊A", "year": "2023",
     "doi": "10.1/a", "source": "OpenAlex", "url": "https://x/1", "oa_url": "https://x/1.pdf"},
    {"title": "文献乙", "authors": [], "journal": None, "year": None,
     "doi": None, "source": "百度学术", "url": "https://x/2", "oa_url": None},
]


def test_collection_lifecycle(client):
    c, h, root = client
    # 创建：清单名带检索关键词
    r = c.post("/web/collections", json={"query": "乡村振兴", "channel": "zh", "search_date": "2026-08-13", "items": _ITEMS}, headers=h)
    data = r.json()
    assert data["ok"] and data["count"] == 2 and "乡村振兴" in data["name"]
    name = data["name"]
    # 重名加序号
    r2 = c.post("/web/collections", json={"query": "乡村振兴", "channel": "zh", "search_date": "2026-08-13", "items": _ITEMS[:1]}, headers=h)
    assert r2.json()["name"] != name
    # 列表（新→旧）
    lst = c.get("/web/collections", headers=h).json()["collections"]
    assert len(lst) == 2 and lst[0]["created_at"] >= lst[1]["created_at"]
    assert lst[0]["query"] == "乡村振兴" and lst[0]["channel"] == "zh"
    # 详情
    detail = c.get(f"/web/collections/{name}", headers=h).json()
    assert detail["items"][0]["title"] == "文献甲"
    # 导出 CSV：UTF-8 BOM + 表头 + 行
    exp = c.post(f"/web/collections/{name}/export", headers=h).json()
    assert exp["ok"] and exp["file"].endswith(".csv")
    raw = (root / "exports" / exp["file"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM（Excel 中文不乱码）
    text = raw.decode("utf-8-sig")
    assert "标题,作者,期刊" in text and "文献甲" in text and "张三" in text
    # 删除：回收站语义
    d = c.delete(f"/web/collections/{name}", headers=h).json()
    assert d["ok"]
    assert not (root / "collections" / f"{name}.json").exists()
    assert len(list((root / "collections" / "_trash").glob("*.json"))) == 1
    assert c.get(f"/web/collections/{name}", headers=h).status_code == 404


def test_collection_empty_rejected(client):
    c, h, _ = client
    r = c.post("/web/collections", json={"query": "x", "items": []}, headers=h)
    assert r.status_code == 400


def test_collection_name_traversal_safe(client):
    """清单名净化：路径穿越字符不入文件名。"""
    c, h, root = client
    r = c.post("/web/collections", json={"query": "../../../etc/evil", "items": _ITEMS[:1]}, headers=h)
    name = r.json()["name"]
    assert "/" not in name and "\\" not in name and ".." not in name
    assert (root / "collections" / f"{name}.json").exists()


# ---- Q4：本地检索双 query 联合 ----


class _FakeEmbedder:
    def check_alive(self):
        return True


class _FakeCloud:
    name = "cloud-fake"

    def translate(self, text, src, tgt):
        return "rural revitalization"


def _mk_m4_search(calls, mapping):
    def fake(root, embedder, query, top_k, doc_hash=None):
        calls.append(query)
        return mapping.get(query, [])
    return fake


def _chunk(cid, score, title="doc"):
    return {"chunk_id": cid, "doc_hash": "h" + cid, "text": "t" + cid, "score": score,
            "doc_meta": {"title": title, "source_file": title}}


def test_search_dual_query_merge(client, monkeypatch):
    """中文 query → 双路检索合并：同 chunk 取高分、结果按分排序、query_en 回传。"""
    from m7_websearch import translate as tr_mod

    tr_mod._CACHE.clear()  # 隔离缓存
    calls = []
    mapping = {
        "乡村振兴": [_chunk("c1", 0.5), _chunk("c2", 0.3)],
        "rural revitalization": [_chunk("c2", 0.9), _chunk("c3", 0.8)],  # c2 双语命中取高分
    }
    monkeypatch.setattr("m4_vectordb.embedder.Embedder", _FakeEmbedder)
    monkeypatch.setattr("m4_vectordb.search.search", _mk_m4_search(calls, mapping))
    monkeypatch.setattr("llm.factory.get_provider", lambda cfg: _FakeCloud())
    c, h, _ = client
    r = c.post("/search", json={"query": "乡村振兴", "top_k": 5}, headers=h)
    data = r.json()
    assert calls == ["乡村振兴", "rural revitalization"]  # 双路
    assert data["query_en"] == "rural revitalization"
    ids = [it["chunk_id"] for it in data["results"]]
    assert ids == ["c2", "c3", "c1"]  # 按合并后分数排序（c2=0.9 高分胜出）
    tr_mod._CACHE.clear()


def test_search_single_query_when_off(client, monkeypatch):
    """完全本地模式（开关关闭）→ 不翻译不触网，单路原词检索。"""
    calls = []
    monkeypatch.setattr("pipeline_core.http_client.web_search_enabled", lambda: False)
    monkeypatch.setattr("m4_vectordb.embedder.Embedder", _FakeEmbedder)
    monkeypatch.setattr("m4_vectordb.search.search", _mk_m4_search(calls, {"乡村振兴": [_chunk("c1", 0.5)]}))

    def _boom(cfg):
        raise AssertionError("完全本地模式不得调用翻译")

    monkeypatch.setattr("llm.factory.get_provider", _boom)
    c, h, _ = client
    r = c.post("/search", json={"query": "乡村振兴", "top_k": 5}, headers=h)
    data = r.json()
    assert calls == ["乡村振兴"] and data["query_en"] is None
    assert len(data["results"]) == 1


def test_translate_cache(monkeypatch):
    """翻译会话缓存：同词第二次不调 provider。"""
    from m7_websearch import translate as tr_mod

    tr_mod._CACHE.clear()
    n = {"count": 0}

    class _Counting(_FakeCloud):
        def translate(self, text, src, tgt):
            n["count"] += 1
            return "cached result"

    monkeypatch.setattr("llm.factory.get_provider", lambda cfg: _Counting())
    r1 = tr_mod.translate_query("媒介融合")
    r2 = tr_mod.translate_query("媒介融合")
    assert r1["translated"] == "cached result" and r2 == r1
    assert n["count"] == 1
    tr_mod._CACHE.clear()
