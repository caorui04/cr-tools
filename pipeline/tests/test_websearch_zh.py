"""T7 中文文献标签测试（蓝图第十四/十七条 + 2026-08-11 智能搜索生成采纳定案）。

覆盖：A2c 调用口径（v2 + site 定向 + 字段铁律 instruction + ref_id 关联）、
A2c→A2b 自动降级（结果带降级标记）、OpenAlex 解析（inverted index 还原 /
language:zh / DOI 前缀剥离 / OA 直链）、DOAJ 解析（bibjson / language:ZH /
fulltext 即 OA）、端点中文通道路由（query_orig + lang + sources）、空结果提示。

全部 monkeypatch http_client 层（post_json/get_json 在适配器模块顶层导入，
patch 模块属性生效），不打真网、不耗 Key 额度。
"""

import pytest
from fastapi.testclient import TestClient

from m7_websearch import registry
from m7_websearch.adapters.doaj import DoajAdapter
from m7_websearch.adapters.openalex import OpenAlexAdapter
from m7_websearch.adapters.qianfan import NOTE_DEGRADED, QianfanScholarAdapter
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


def _fail(error="HTTP 错误 500"):
    return NetResult(ok=False, status=500, data=None, error=error, elapsed=0.1)


# ---- 千帆 A2c 主通道（A/C 1/A/C 5）----

# 实测定案形态：choices[0].message.content = instruction 产出的结构化 JSON；references 带 id→url
_A2C_RESP = {
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {
                "role": "assistant",
                "content": '[{"title": "媒介融合研究综述", "authors": ["张三", "李四"], "journal": "新闻大学", "year": "2023", "abstract": "本文综述媒介融合研究进展。", "ref_id": 2}, {"title": "信息不全条目", "authors": [], "journal": null, "year": null, "abstract": null, "ref_id": 5}]',
            },
        }
    ],
    "references": [
        {"id": 1, "title": "无关网页", "url": "https://xueshu.baidu.com/x1", "content": "..."},
        {"id": 2, "title": "媒介融合研究综述", "url": "https://xueshu.baidu.com/detail/abc", "content": "..."},
    ],
}


def test_a2c_payload_and_parse(monkeypatch):
    """A/C 5 口径：v2 + site 定向 + 字段铁律 instruction；ref_id 关联详情页 URL。"""
    calls = {}

    def fake_post(url, payload, *, timeout, headers, redact):
        calls["url"] = url
        calls["payload"] = payload
        calls["redact"] = redact
        return _ok(_A2C_RESP)

    monkeypatch.setattr("m7_websearch.adapters.qianfan.post_json", fake_post)
    results = QianfanScholarAdapter().search("media convergence", 8, query_orig="媒介融合")

    p = calls["payload"]
    assert calls["url"].endswith("/v2/ai_search/chat/completions")
    assert p["model"] == "ernie-4.5-turbo-32k"
    assert p["search_source"] == "baidu_search_v2"
    assert p["search_filter"]["match"]["site"] == ["xueshu.baidu.com"]
    assert p["messages"] == [{"role": "user", "content": "媒介融合"}]  # 中文原词直搜
    # 字段铁律 + ref_id 关联（实测定案口径）
    assert "字段铁律" in p["instruction"] and "ref_id" in p["instruction"] and "null" in p["instruction"]
    assert p["enable_corner_markers"] is False
    assert calls["redact"]  # Key 脱敏

    # A/C 1：结构化条目；无信息字段 null 不瞎编
    assert len(results) == 2
    r0 = results[0]
    assert r0.title == "媒介融合研究综述" and r0.source == "百度学术"
    assert r0.authors == ["张三", "李四"] and r0.journal == "新闻大学" and r0.year == "2023"
    assert r0.abstract == "本文综述媒介融合研究进展。"
    assert r0.url == "https://xueshu.baidu.com/detail/abc"  # ref_id=2 → references URL
    assert r0.doi is None  # A2c 无 DOI（已知局限）
    r1 = results[1]
    assert r1.journal is None and r1.year is None and r1.abstract is None and r1.authors == []
    assert r1.url == ""  # ref_id=5 不在 references → 空串不瞎编


def test_a2c_fail_fallback_a2b(monkeypatch):
    """A/C 2：模型服务不可用 → 自动降级 A2b，结果带降级标记。"""
    calls = []

    def fake_post(url, payload, *, timeout, headers, redact):
        calls.append(url)
        if url.endswith("/chat/completions"):
            return _fail()
        return _ok(
            {
                "references": [
                    {"id": 1, "type": "web", "title": "网页片段条目", "url": "https://xueshu.baidu.com/x", "content": "片段内容" * 100},
                    {"id": 2, "type": "image", "title": "图片结果应剔除", "url": "https://img", "content": ""},
                ]
            }
        )

    monkeypatch.setattr("m7_websearch.adapters.qianfan.post_json", fake_post)
    results = QianfanScholarAdapter().search("x", 8, query_orig="媒介融合")

    assert [u.split("/")[-1] for u in calls] == ["completions", "web_search"]  # 先 A2c 后 A2b
    assert len(results) == 1
    r = results[0]
    assert r.title == "网页片段条目" and r.note == NOTE_DEGRADED
    assert len(r.abstract) <= 300  # 片段截断


def test_a2c_bad_output_fallback_a2b(monkeypatch):
    """A2c 输出非 JSON 数组（幻觉/围栏异常）→ 同样降级 A2b。"""
    def fake_post(url, payload, *, timeout, headers, redact):
        if url.endswith("/chat/completions"):
            return _ok({"choices": [{"message": {"content": "很抱歉，我无法回答。", "role": "assistant"}}]})
        return _ok({"references": [{"id": 1, "type": "web", "title": "降级条目", "url": "u", "content": "c"}]})

    monkeypatch.setattr("m7_websearch.adapters.qianfan.post_json", fake_post)
    results = QianfanScholarAdapter().search("x", 8, query_orig="媒介融合")
    assert len(results) == 1 and results[0].note == NOTE_DEGRADED


# ---- OpenAlex 归并（A/C 3）----

_OPENALEX_RESP = {
    "results": [
        {
            "display_name": "乡村振兴研究",
            "authorships": [{"author": {"display_name": "王五"}}, {"author": {"display_name": ""}}],
            "primary_location": {"source": {"display_name": "中国农村经济"}, "landing_page_url": "https://journal.cn/123"},
            "publication_year": 2022,
            "abstract_inverted_index": {"本文": [0], "研究": [1], "乡村": [2], "振兴": [3]},
            "doi": "https://doi.org/10.1234/cn.123",
            "best_oa_location": {"pdf_url": "https://journal.cn/123.pdf"},
            "id": "https://openalex.org/W123",
        }
    ]
}


def test_openalex_parse_and_lang_filter(monkeypatch):
    """英译词 + filter=language:zh；inverted index 还原；DOI 前缀剥离；OA 直链。"""
    calls = {}

    def fake_get(url, *, timeout, params):
        calls["url"] = url
        calls["params"] = params
        return _ok(_OPENALEX_RESP)

    monkeypatch.setattr("m7_websearch.adapters.openalex.get_json", fake_get)
    results = OpenAlexAdapter().search("rural revitalization", 8, query_orig="乡村振兴", lang="zh")

    assert calls["url"] == "https://api.openalex.org/works"
    assert calls["params"]["search"] == "rural revitalization"  # 用英译词，不用中文原词
    assert calls["params"]["filter"] == "language:zh"
    assert calls["params"]["api_key"]  # Key 从 web_search.openalex 节加载

    r = results[0]
    assert r.title == "乡村振兴研究" and r.source == "OpenAlex"
    assert r.authors == ["王五"]  # 空名剔除
    assert r.journal == "中国农村经济" and r.year == "2022"
    assert r.abstract == "本文 研究 乡村 振兴"
    assert r.doi == "10.1234/cn.123"  # https://doi.org/ 前缀剥离
    assert r.oa_url == "https://journal.cn/123.pdf" and r.url == "https://journal.cn/123"


def test_openalex_no_lang_for_intl(monkeypatch):
    """国际通道（lang=None）不带 filter（T6 复用形态）。"""
    calls = {}

    def fake_get(url, *, timeout, params):
        calls["params"] = params
        return _ok({"results": []})

    monkeypatch.setattr("m7_websearch.adapters.openalex.get_json", fake_get)
    OpenAlexAdapter().search("rural", 8)
    assert "filter" not in calls["params"]


# ---- DOAJ 归并（A/C 3）----

_DOAJ_RESP = {
    "results": [
        {
            "id": "abc123",
            "bibjson": {
                "title": "乡村振兴路径研究",
                "author": [{"name": "赵六"}],
                "journal": {"title": "某 OA 期刊"},
                "year": "2021",
                "abstract": "DOAJ 摘要",
                "identifier": [{"type": "doi", "id": "10.5678/doaj.1"}, {"type": "pissn", "id": "1234-5678"}],
                "link": [{"type": "fulltext", "url": "https://oa.cn/full.pdf", "content_type": "application/pdf"}],
            },
        }
    ]
}


def test_doaj_parse_and_lang_filter(monkeypatch):
    """Lucene 语言过滤 bibjson.language:ZH；fulltext 即 OA 直链；doi identifier 提取。"""
    calls = {}

    def fake_get(url, *, timeout, params):
        calls["url"] = url
        return _ok(_DOAJ_RESP)

    monkeypatch.setattr("m7_websearch.adapters.doaj.get_json", fake_get)
    results = DoajAdapter().search("rural revitalization", 8, lang="zh")

    from urllib.parse import unquote

    decoded = unquote(calls["url"])  # query 经 quote 编码进路径段，断言前先解码
    assert "(rural revitalization) AND bibjson.journal.language:ZH" in decoded
    assert calls["url"].startswith("https://doaj.org/api/v4/search/articles/")

    r = results[0]
    assert r.title == "乡村振兴路径研究" and r.source == "DOAJ"
    assert r.authors == ["赵六"] and r.journal == "某 OA 期刊" and r.year == "2021"
    assert r.abstract == "DOAJ 摘要"
    assert r.doi == "10.5678/doaj.1"
    assert r.oa_url == "https://oa.cn/full.pdf"  # DOAJ 收录即 OA
    assert r.url == "https://doaj.org/article/abc123"


# ---- 端点中文通道（A/C 2/A/C 4）----


class _CaptureAdapter:
    """捕获 query/query_orig/lang 的占位适配器（id 占住中文通道源位）。"""

    captured: list[dict] = []

    def __init__(self, id, label):
        self.id = id
        self.label = label

    def search(self, query, max_results, *, query_orig=None, lang=None):
        self.captured.append({"id": self.id, "query": query, "query_orig": query_orig, "lang": lang})
        return [WebResult(title=f"{self.label}-条目", source=self.label, url="https://x")]


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def test_endpoint_zh_channel_routing(client):
    """channel=zh：三源扇出；千帆收中文原词、OpenAlex/DOAJ 收英译词 + lang=zh。"""
    _CaptureAdapter.captured = []
    for aid, label in (("qianfan_scholar", "百度学术"), ("openalex", "OpenAlex"), ("doaj", "DOAJ")):
        registry.register_adapter(_CaptureAdapter(aid, label))
    c, h = client
    r = c.post(
        "/web/search",
        json={"query": "媒介融合", "query_en": "media convergence", "channel": "zh"},
        headers=h,
    )
    data = r.json()
    assert data["ok"] and data["channel"] == "zh" and data["count"] == 3
    assert data["empty_hint"] == ""

    by_id = {c0["id"]: c0 for c0 in _CaptureAdapter.captured}
    assert set(by_id) == {"qianfan_scholar", "openalex", "doaj"}
    assert by_id["qianfan_scholar"]["query_orig"] == "媒介融合"  # 中文原词给中文源
    assert by_id["openalex"]["lang"] == "zh" and by_id["doaj"]["lang"] == "zh"
    assert all(c0["query"] == "media convergence" for c0 in _CaptureAdapter.captured)
    assert data["sources"] == ["DOAJ", "OpenAlex", "百度学术"]


def test_endpoint_empty_hint(client):
    """A/C 4：无命中时 empty_hint 提示换关键词（非静默空白）。"""
    c, h = client  # 注册表已清空 → 无适配器 → 空结果
    r = c.post("/web/search", json={"query": "x", "query_en": "nonexistent-xyz"}, headers=h)
    data = r.json()
    assert data["count"] == 0 and "更换关键词" in data["empty_hint"]


def test_schema_note_field():
    """schema 扩展 note 字段（降级标记）进入 to_dict。"""
    assert WebResult(title="t", source="s", note="n").to_dict()["note"] == "n"
    assert WebResult(title="t", source="s").to_dict()["note"] is None
