"""T6 国际文献标签测试（蓝图第十五/十六/十七条定案）。

覆盖：arXiv 适配器解析（Atom XML → 统一 schema，全 OA：pdf 直链入 oa_url）、
DOI/标题去重合并（OA 优先换留、顺序稳定）、端点国际通道路由（三源扇出 +
去重 + lang=None）、旧 /web/literature_search 门面复用适配器（agent/webui 兼容）。

全部 monkeypatch http_client 层，不打真网。
"""

import pytest
from fastapi.testclient import TestClient

from m7_websearch import registry
from m7_websearch.adapters.arxiv import ArxivAdapter
from m7_websearch.registry import dedup_results
from m7_websearch.schema import WebResult
from pipeline_core.config import PipelineConfig
from pipeline_core.http_client import NetResult
from server.routes import create_app


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """清空注册表 + 禁用配置驱动的内置适配器加载（真实源绝不打真网）。"""
    monkeypatch.setattr("m7_websearch.config.enabled_builtin_adapters", lambda: [])
    registry.clear_registry()
    yield
    registry.clear_registry()


def _ok(data):
    return NetResult(ok=True, status=200, data=data, error="", elapsed=0.1)


# ---- arXiv 适配器（A/C 5 迁入）----

_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>  Rural Revitalization:
   A Survey  </title>
    <summary> We survey rural revitalization studies. </summary>
    <published>2024-03-01T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <link rel="alternate" href="https://arxiv.org/abs/2403.00001"/>
    <link title="pdf" href="https://arxiv.org/pdf/2403.00001"/>
    <arxiv:doi>10.1234/arxiv.1</arxiv:doi>
  </entry>
  <entry>
    <title>No DOI Entry</title>
    <summary>Second abstract.</summary>
    <published>2023-01-15T00:00:00Z</published>
    <author><name>Carol</name></author>
    <link rel="alternate" href="https://arxiv.org/abs/2301.00002"/>
    <link title="pdf" href="https://arxiv.org/pdf/2301.00002"/>
  </entry>
</feed>
"""


def test_arxiv_adapter_parse(monkeypatch):
    """Atom XML → 统一 schema；title 折叠空白；全 OA：pdf 入 oa_url、摘要页入 url；arxiv:doi 提取。"""
    calls = {}

    def fake_get(url, *, timeout, params=None):
        calls["url"] = url
        return _ok(_ARXIV_XML)

    monkeypatch.setattr("m7_websearch.adapters.arxiv.get_json", fake_get)
    results = ArxivAdapter().search("rural revitalization", 8)

    assert "export.arxiv.org/api/query" in calls["url"]
    assert "rural%20revitalization" in calls["url"]
    assert len(results) == 2
    r0 = results[0]
    assert r0.title == "Rural Revitalization: A Survey"  # 换行/多余空白折叠
    assert r0.source == "arXiv"
    assert r0.authors == ["Alice Zhang", "Bob Li"]
    assert r0.year == "2024"
    assert r0.abstract.startswith("We survey")
    assert r0.url == "https://arxiv.org/abs/2403.00001"
    assert r0.oa_url == "https://arxiv.org/pdf/2403.00001"  # arXiv 全 OA
    assert r0.doi == "10.1234/arxiv.1"
    assert results[1].doi is None


def test_arxiv_adapter_failure_isolated(monkeypatch):
    """请求失败/非 XML → 归一空列表不抛异常。"""
    monkeypatch.setattr(
        "m7_websearch.adapters.arxiv.get_json",
        lambda url, *, timeout, params=None: NetResult(ok=False, status=None, data=None, error="连接失败", elapsed=0.1),
    )
    assert ArxivAdapter().search("x", 8) == []
    monkeypatch.setattr(
        "m7_websearch.adapters.arxiv.get_json",
        lambda url, *, timeout, params=None: _ok("not xml <<<"),
    )
    assert ArxivAdapter().search("x", 8) == []


# ---- 去重合并（A/C 2）----


def test_dedup_by_doi_and_title():
    """同 DOI（大小写差异）/ 同标题（空白差异）不重复；无 DOI 按标题键。"""
    a = WebResult(title="Same Paper", source="OpenAlex", doi="10.1/ABC")
    b = WebResult(title="另一标题", source="DOAJ", doi="10.1/abc")  # 同 DOI 不同写法
    c = WebResult(title="Same  Paper", source="arXiv")  # 与 a 同标题但 a 有 DOI → 不同键
    d = WebResult(title="same paper", source="DOAJ")  # 与 a 标题同（大小写）但键不同（a 走 DOI 键）
    out = dedup_results([a, b, c, d])
    # a/b 同 DOI 键留 1；c/d 标题键归一留 1（c 先出现）
    assert len(out) == 2
    assert out[0].doi == "10.1/ABC"
    assert out[1].title == "Same  Paper"


def test_dedup_oa_preference():
    """同键后者带 OA 而前者没有 → 换成带 OA 的（T8 一键下载仅对 OA 开放）。"""
    no_oa = WebResult(title="P", source="OpenAlex", doi="10.1/x")
    with_oa = WebResult(title="P", source="DOAJ", doi="10.1/x", oa_url="https://oa/p.pdf", url="https://x")
    out = dedup_results([no_oa, with_oa])
    assert len(out) == 1 and out[0].oa_url == "https://oa/p.pdf"
    # 顺序稳定：OA 优先不逆转既有顺序
    out2 = dedup_results([with_oa, no_oa])
    assert len(out2) == 1 and out2[0].oa_url == "https://oa/p.pdf"


# ---- 端点国际通道（A/C 1/A/C 3）----


class _StubAdapter:
    def __init__(self, id, label, results):
        self.id = id
        self.label = label
        self._results = results
        self.calls = []

    def search(self, query, max_results, *, query_orig=None, lang=None):
        self.calls.append({"query": query, "lang": lang})
        return self._results


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def test_endpoint_intl_channel_fanout_dedup(client):
    """channel=intl：三源扇出 + DOI 去重 + lang=None + 来源标签/OA 标记。"""
    oa_dup = WebResult(title="Dup Paper", source="DOAJ", doi="10.1/dup", oa_url="https://oa/dup.pdf")
    adapters = [
        _StubAdapter("openalex", "OpenAlex", [
            WebResult(title="Dup Paper", source="OpenAlex", doi="10.1/dup", url="https://oa.org/1"),
            WebResult(title="OpenAlex Only", source="OpenAlex", doi="10.1/only"),
        ]),
        _StubAdapter("doaj", "DOAJ", [oa_dup]),
        _StubAdapter("arxiv", "arXiv", [WebResult(title="Arxiv Only", source="arXiv", oa_url="https://arxiv.org/pdf/1")]),
    ]
    for a in adapters:
        registry.register_adapter(a)
    c, h = client
    r = c.post(
        "/web/search",
        json={"query": "乡村振兴", "query_en": "rural revitalization", "channel": "intl"},
        headers=h,
    )
    data = r.json()
    assert data["ok"] and data["channel"] == "intl"
    # 三源都收到英译词且 lang=None（国际通道无语言过滤）
    for a in adapters:
        assert a.calls == [{"query": "rural revitalization", "lang": None}]
    # DOI 去重：4 条原始 → 3 条；OpenAlex 的 Dup（无 OA）被 DOAJ 的 Dup（带 OA）替换
    assert data["count"] == 3
    titles = [it["title"] for it in data["results"]]
    assert titles.count("Dup Paper") == 1
    dup = next(it for it in data["results"] if it["title"] == "Dup Paper")
    assert dup["source"] == "DOAJ" and dup["oa_url"] == "https://oa/dup.pdf"
    assert data["sources"] == ["DOAJ", "OpenAlex", "arXiv"]


def test_legacy_literature_search_facade(client, monkeypatch):
    """旧 arXiv 端点 = 兼容门面：复用适配器，输出旧契约（agent kb_lit_search 用）。"""
    monkeypatch.setattr(
        "m7_websearch.adapters.arxiv.get_json",
        lambda url, *, timeout, params=None: _ok(_ARXIV_XML),
    )
    c, h = client
    r = c.post("/web/literature_search", json={"topic": "rural revitalization", "max_results": 8}, headers=h)
    data = r.json()
    assert data["ok"] and data["enabled"] and data["source"] == "arXiv 开放 API"
    assert len(data["results"]) == 2
    item = data["results"][0]
    assert set(item) == {"title", "authors", "year", "url", "abstract"}  # 旧契约字段
    assert item["url"] == "https://arxiv.org/abs/2403.00001"
