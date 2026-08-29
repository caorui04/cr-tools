"""T5 联网检索适配层测试（蓝图第十六/十七/十八条定案）。

覆盖：统一 schema 字段、注册框架（新增适配器不改框架代码）、单源失败隔离、
来源筛选、Key 从 web_search 节加载、英译降级链（云端→本地→不翻译+提示）、
端点门控（完全本地模式整体不可用）、query_en 跳过翻译。

全部 mock 不打真网：provider / LlamaCppChatProvider / web_search_enabled 均 monkeypatch
（目标函数均为运行时 from-import，patch 生效）。
"""

import pytest
from fastapi.testclient import TestClient

from m7_websearch import registry
from m7_websearch.adapters.mock import MockAdapter
from m7_websearch.config import web_search_available, web_search_config
from m7_websearch.schema import WebResult
from m7_websearch.translate import HINT_NO_TRANSLATE, contains_cjk, translate_query
from pipeline_core.config import PipelineConfig
from server.routes import create_app


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """每个用例前后清空注册表 + 禁用配置驱动的内置适配器加载（防真实源打真网）
    + 清翻译会话缓存（Q4 引入，防跨用例缓存命中跳过被 patch 的 provider）。"""
    from m7_websearch import translate as tr_mod

    monkeypatch.setattr("m7_websearch.config.enabled_builtin_adapters", lambda: [])
    tr_mod._CACHE.clear()
    registry.clear_registry()
    yield
    registry.clear_registry()
    tr_mod._CACHE.clear()


# ---- schema ----


def test_schema_to_dict_fields():
    """统一 schema 含定案全字段：标题/作者/期刊/年份/摘要/DOI/来源/OA 链接/原文链接。"""
    d = WebResult(title="t", source="s").to_dict()
    for f in ("title", "authors", "journal", "year", "abstract", "doi", "source", "url", "oa_url"):
        assert f in d
    # 无信息字段默认 None/空（字段铁律：不瞎编）
    assert d["journal"] is None and d["oa_url"] is None and d["authors"] == []


# ---- 注册框架（A/C 1）----


def test_register_adapter_and_search():
    """新增适配器仅实现接口 + register_adapter → 出现在结果中并带来源标签。"""
    registry.register_adapter(MockAdapter())
    results = registry.run_search("anything", 8)
    assert len(results) == 2
    assert all(r.source == "Mock 模拟源" for r in results)
    assert results[0].oa_url and results[1].oa_url is None


def test_single_adapter_failure_isolated():
    """单源异常归一为空 + 不拖垮其他源。"""

    class BoomAdapter:
        id = "boom"
        label = "Boom"

        def search(self, query, max_results, *, query_orig=None, lang=None):
            raise RuntimeError("network down")

    registry.register_adapter(BoomAdapter())
    registry.register_adapter(MockAdapter())
    results = registry.run_search("q", 8)
    assert len(results) == 2  # mock 正常返回


def test_sources_filter():
    """sources 子集 = 来源筛选。"""
    registry.register_adapter(MockAdapter())
    assert registry.run_search("q", 8, sources=["nonexistent"]) == []
    assert len(registry.run_search("q", 8, sources=["mock"])) == 2


# ---- Key 加载（A/C 4）----


def test_web_search_config_loads_keys():
    """Key 从权威 config.yaml web_search 节加载（千帆/OpenAlex 节存在且含 api_key）。"""
    cfg = web_search_config()
    assert cfg.get("qianfan", {}).get("api_key")
    assert cfg.get("openalex", {}).get("api_key")
    assert cfg.get("openalex", {}).get("base_url") == "https://api.openalex.org"


# ---- 英译降级链（A/C 3）----


def test_contains_cjk():
    assert contains_cjk("乡村振兴")
    assert not contains_cjk("rural revitalization")
    assert not contains_cjk("")


def test_translate_non_cjk_passthrough():
    """英文关键词不翻译：method none + 原文直交 + 无提示。"""
    r = translate_query("rural revitalization")
    assert r == {"original": "rural revitalization", "translated": "rural revitalization", "method": "none", "hint": ""}


class _FakeCloudProvider:
    name = "cloud-fake"

    def translate(self, text, src, tgt):
        assert (src, tgt) == ("zh", "en")
        return "rural revitalization"


def test_translate_cloud_success(monkeypatch):
    """云端可用 → method cloud（不触碰本地）。"""
    monkeypatch.setattr("llm.factory.get_provider", lambda cfg: _FakeCloudProvider())
    r = translate_query("乡村振兴")
    assert r["method"] == "cloud" and r["translated"] == "rural revitalization" and r["hint"] == ""


class _FakeLocalProvider:
    def __init__(self, base_url, model_name):
        pass

    def check_alive(self):
        return True

    def translate(self, text, src, tgt):
        return "local translation"


def test_translate_cloud_fail_local_success(monkeypatch):
    """云端失败 → 本地已加载 → method local。"""
    def _raise(cfg):
        raise RuntimeError("cloud down")

    monkeypatch.setattr("llm.factory.get_provider", _raise)
    monkeypatch.setattr("llm.llama_chat_provider.LlamaCppChatProvider", _FakeLocalProvider)
    r = translate_query("乡村振兴")
    assert r["method"] == "local" and r["translated"] == "local translation"


def test_translate_all_fail_hint(monkeypatch):
    """云端失败 + 本地未加载 → 不翻译直接提交 + 定案提示文案。"""

    class _DeadLocal(_FakeLocalProvider):
        def check_alive(self):
            return False

    def _raise(cfg):
        raise RuntimeError("cloud down")

    monkeypatch.setattr("llm.factory.get_provider", _raise)
    monkeypatch.setattr("llm.llama_chat_provider.LlamaCppChatProvider", _DeadLocal)
    r = translate_query("乡村振兴")
    assert r["method"] == "none" and r["translated"] == "乡村振兴" and r["hint"] == HINT_NO_TRANSLATE


# ---- 端点（A/C 2/A/C 5）----


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def test_endpoint_disabled_when_off(client, monkeypatch):
    """完全本地模式：status/translate/search 全部返回 enabled=False（A/C 5）。"""
    monkeypatch.setattr("pipeline_core.http_client.web_search_enabled", lambda: False)
    c, h = client
    assert not web_search_available()

    r = c.get("/web/search_status", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["message"]

    r = c.post("/web/translate_query", json={"query": "乡村振兴"}, headers=h)
    assert r.json()["enabled"] is False

    r = c.post("/web/search", json={"query": "乡村振兴"}, headers=h)
    assert r.json()["ok"] is False and r.json()["enabled"] is False


def test_endpoint_search_with_mock_adapter(client):
    """统一检索端点：注册 mock 适配器 → 结果带统一 schema + 来源标签 + 检索日期。"""
    registry.register_adapter(MockAdapter())
    c, h = client
    r = c.post("/web/search", json={"query": "rural", "query_en": "rural revitalization"}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] and data["enabled"]
    assert data["query_en"] == "rural revitalization"
    assert data["translation_method"] == "none"  # query_en 已给，跳过翻译
    assert data["sources"] == ["Mock 模拟源"]
    assert data["count"] == 2
    item = data["results"][0]
    for f in ("title", "authors", "journal", "year", "abstract", "doi", "source", "url", "oa_url"):
        assert f in item
    assert data["search_date"] and "仅供文献发现" in data["disclaimer"]


def test_endpoint_search_translates_cjk(client, monkeypatch):
    """中文 query 无 query_en → 走翻译链并回传翻译结果（界面显示「已翻译为」）。"""
    monkeypatch.setattr("llm.factory.get_provider", lambda cfg: _FakeCloudProvider())
    registry.register_adapter(MockAdapter())
    c, h = client
    r = c.post("/web/search", json={"query": "乡村振兴"}, headers=h)
    data = r.json()
    assert data["query_en"] == "rural revitalization" and data["translation_method"] == "cloud"


def test_endpoint_translate_query(client, monkeypatch):
    """翻译端点：返回 original/translated/method/hint 四字段。"""
    monkeypatch.setattr("llm.factory.get_provider", lambda cfg: _FakeCloudProvider())
    c, h = client
    r = c.post("/web/translate_query", json={"query": "媒介融合"}, headers=h)
    data = r.json()
    assert data["ok"] and data["original"] == "媒介融合"
    assert data["translated"] == "rural revitalization" and data["method"] == "cloud"
