"""json_utils.py 单测（S8，批次 P）。

覆盖：
(a) 围栏剥离各形态：纯 JSON / ```json 围栏 / 裸 ``` 围栏 / 前后噪文 / 坏 JSON；
(b) validate_biblio 结构补全（11 biblio 键 + 9 confidence 键，接受 dict 或 str）；
(c) null_biblio 统一降级结构；
(d) 4 provider 行为等价：stub 直出 null_biblio；llama/gguf/cloud 共用同一解析路径；
(e) GgufProvider._chat_for_json（修 C1）：协议存在 + 双消息拼接 + 围栏容忍解析。
"""

import json

import pytest

from llm.gguf_provider import GgufProvider
from llm.json_utils import (
    BIBLIO_KEYS,
    CONF_KEYS,
    null_biblio,
    parse_json_obj,
    strip_json_fence,
    validate_biblio,
)
from llm.stub_provider import StubProvider


# ---- (a) 围栏剥离 ----

def test_strip_plain_json():
    assert parse_json_obj('{"a": 1}') == {"a": 1}


def test_strip_json_fence():
    text = '```json\n{"a": 1}\n```'
    assert parse_json_obj(text) == {"a": 1}


def test_strip_bare_fence():
    text = '```\n{"a": 1}\n```'
    assert parse_json_obj(text) == {"a": 1}


def test_strip_noise_around_fence():
    text = '好的，以下是结果：\n```json\n{"a": 1}\n```\n以上。'
    assert parse_json_obj(text) == {"a": 1}


def test_bad_json_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_obj("这不是 JSON")


def test_strip_no_fence_returns_stripped():
    assert strip_json_fence('  {"a": 1}  ') == '{"a": 1}'


# ---- (b)(c) biblio 结构 ----

def test_validate_biblio_fills_missing():
    raw = {"biblio": {"title": "文甲"}, "field_confidence": {"title": 0.9}}
    out = validate_biblio(raw)
    assert set(out["biblio"].keys()) == set(BIBLIO_KEYS)
    assert set(out["field_confidence"].keys()) == set(CONF_KEYS)
    assert out["biblio"]["title"] == "文甲"
    assert out["biblio"]["doi"] is None
    assert out["field_confidence"]["title"] == 0.9
    assert out["field_confidence"]["doi"] == 0.0


def test_validate_biblio_accepts_str():
    out = validate_biblio('{"biblio": {"title": "文甲"}}')
    assert out["biblio"]["title"] == "文甲"


def test_null_biblio_structure():
    nb = null_biblio()
    assert all(v is None for v in nb["biblio"].values())
    assert all(v == 0.0 for v in nb["field_confidence"].values())
    assert set(nb["biblio"].keys()) == set(BIBLIO_KEYS)
    assert set(nb["field_confidence"].keys()) == set(CONF_KEYS)


# ---- (d) provider 等价 ----

def test_stub_matches_null_biblio():
    """StubProvider.extract_biblio 即 null_biblio（4 处统一后的结构等价）。"""
    assert StubProvider().extract_biblio("任意文本", "APA") == null_biblio()


def test_providers_share_parse_path():
    """llama/gguf/cloud 的 JSON 解析均指向 json_utils.parse_json_obj（grep 级等价的运行时佐证）。"""
    import inspect

    from llm import cloud_provider, gguf_provider, llama_chat_provider

    for mod in (llama_chat_provider, gguf_provider, cloud_provider):
        src = inspect.getsource(mod)
        assert "parse_json_obj" in src
        assert "```json" not in src  # 围栏逻辑已收拢，provider 内无残留
        assert "_null_biblio" not in src and "_validate_biblio" not in src


# ---- (e) GgufProvider._chat_for_json（修 C1）----

def test_gguf_has_chat_for_json_protocol():
    """digest/verify duck-typing 入口存在（此前缺失 → prefer=gguf 必降级）。"""
    p = GgufProvider(model_name="m", port=19999)
    assert callable(getattr(p, "_chat_for_json", None))


def test_gguf_chat_for_json_calls_transport(monkeypatch):
    """system+JSON 约束+user 拼接为单 prompt，经 completion 传输，围栏容忍解析。"""
    p = GgufProvider(model_name="m", port=19999)
    seen = {}

    def fake_completion(prompt: str) -> str:
        seen["prompt"] = prompt
        return '```json\n{"heading": "概览", "points": []}\n```'

    monkeypatch.setattr(p, "_llamacpp_completion", fake_completion)
    out = p._chat_for_json("SYS", "USER")
    assert out == {"heading": "概览", "points": []}
    assert "SYS" in seen["prompt"] and "USER" in seen["prompt"]
    assert "请仅输出 JSON" in seen["prompt"]


def test_gguf_chat_for_json_ollama_transport(monkeypatch):
    """ollama 后端走 _ollama_generate，纯 JSON 直解析。"""
    p = GgufProvider(model_name="m", server="ollama", port=19999)
    monkeypatch.setattr(p, "_ollama_generate", lambda prompt: '{"verdict": "supported"}')
    assert p._chat_for_json("SYS", "USER") == {"verdict": "supported"}
