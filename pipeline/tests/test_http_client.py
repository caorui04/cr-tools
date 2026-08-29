"""http_client.py 单测（U4 网络访问统一封装契约第 3 节验收用例，批次 F 末项）。

覆盖契约验收点（arXiv 联网烟测由批次验证环节执行，不进单测）：
(a) 超时/连接归一：不可达地址 + timeout=1 → ok=False、归一文案、不抛异常；
(b) 脱敏：redact 值不出现在日志（替换为 ***）；
(c) C5 回归：chdir 后 web_search_enabled() 结果不变（权威路径不随 cwd）；
(d) 正常 get_json：本地回环 http.server 返回 JSON → ok=True、data 解析正确；
(e) 归一文案补充：HTTP 错误码 / JSON 解析失败两类。
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pipeline_core import http_client
from pipeline_core.http_client import NetResult, get_json, post_json, web_search_enabled


# ---- 本地回环简易 HTTP 服务（不依赖外网）----


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, content_type: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/json":
            self._send(200, json.dumps({"hello": "world"}), "application/json")
        elif self.path == "/badjson":
            self._send(200, "{not valid json", "application/json")
        elif self.path == "/missing":
            self._send(404, "not found", "text/plain")
        else:
            self._send(200, "plain text", "text/plain")

    def log_message(self, *args):  # 静音请求日志
        pass


@pytest.fixture(scope="module")
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    t.join(timeout=5)


# ---- (d) 正常 get_json ----


def test_get_json_ok(local_server):
    r = get_json(f"{local_server}/json")
    assert isinstance(r, NetResult)
    assert r.ok is True
    assert r.status == 200
    assert r.data == {"hello": "world"}
    assert r.error == ""
    assert r.elapsed >= 0


def test_get_json_non_json_returns_text(local_server):
    """非 JSON 响应 data 为原始文本（arXiv Atom XML 场景）。"""
    r = get_json(f"{local_server}/text")
    assert r.ok is True
    assert r.data == "plain text"


# ---- (e) 归一文案：HTTP 错误码 / JSON 解析失败 ----


def test_http_error_normalized(local_server):
    r = get_json(f"{local_server}/missing")
    assert r.ok is False
    assert r.status == 404
    assert r.error == "HTTP 错误 404"
    assert r.data is None


def test_json_parse_failure_normalized(local_server):
    r = get_json(f"{local_server}/badjson")
    assert r.ok is False
    assert r.status == 200
    assert r.error == "响应 JSON 解析失败"


# ---- (a) 超时/连接归一：不可达地址 + timeout=1，不抛异常 ----


def test_unreachable_normalized_no_raise():
    # 10.255.255.1 为保留网段（不可路由）；视环境归一为超时或连接失败，二者皆合法文案
    r = get_json("http://10.255.255.1/", timeout=1)
    assert r.ok is False
    assert r.status is None
    assert r.data is None
    assert ("超时" in r.error) or ("连接失败" in r.error)
    assert r.elapsed >= 0


# ---- (b) 脱敏：redact 值不出现日志 ----


def test_redact_value_not_in_log(caplog):
    secret = "sk-test-SECRET-12345"
    with caplog.at_level(logging.INFO, logger="pipeline_core.http_client"):
        # 127.0.0.1:1 必连接失败 → 走归一日志路径；秘密值嵌在 query 里
        r = post_json(f"http://127.0.0.1:1/cb?key={secret}", {"a": 1}, timeout=1, redact=[secret])
    assert r.ok is False
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret not in log_text
    assert "***" in log_text


# ---- (c) C5 回归：chdir 后 web_search_enabled() 结果不变 ----


def test_web_search_enabled_cwd_independent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("analysis:\n  web_search_enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(http_client, "_CONFIG_PATH", cfg)
    # chdir 到无关目录：结果必须不变（原 Path.cwd() 实现此处会丢配置）
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert web_search_enabled() is False

    cfg.write_text("analysis:\n  web_search_enabled: true\n", encoding="utf-8")
    assert web_search_enabled() is True


def test_web_search_enabled_default_true_when_missing(tmp_path, monkeypatch):
    """config.yaml 缺失/无 analysis 键 → 默认开启（与原实现同语义）。"""
    monkeypatch.setattr(http_client, "_CONFIG_PATH", tmp_path / "nope.yaml")
    assert web_search_enabled() is True

    cfg = tmp_path / "config.yaml"
    cfg.write_text("http_port: 8737\n", encoding="utf-8")
    monkeypatch.setattr(http_client, "_CONFIG_PATH", cfg)
    assert web_search_enabled() is True
