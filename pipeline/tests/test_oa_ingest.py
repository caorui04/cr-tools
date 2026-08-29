"""T8 OA 一键下载入库测试（蓝图第十一/十四条：仅对 OA 命中开放；轻量直入 M4 用户拍板）。

覆盖：U4 download_file（成功写盘/超限拒绝/HTTP 错误归一/.part 清理）、
oa_ingest 端点（成功 queued + sidecar 来源声明 + md 落 03 + 原件归档、
内容判重 duplicate、非 PDF 422、扫描件 422、下载失败 502、完全本地门控）。

下载层全部 monkeypatch（httpx.stream / download_file），不打真网；
PDF 用 fitz 现场生成真实文件（含文本层 / 无文本层两种）。
"""

import pytest
from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from pipeline_core.http_client import NetResult, download_file
from server.routes import create_app


def _make_pdf(path, text: str):
    """fitz 生成真实 PDF（text 为空 = 无文本层扫描件形态）。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


# ---- U4 download_file ----


class _FakeStreamResp:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, chunk_size=65536):
        yield from self._chunks


def test_download_file_success(tmp_path, monkeypatch):
    """流式写盘 + 返回字节数 + .part 清理。"""
    monkeypatch.setattr(
        "pipeline_core.http_client.httpx.stream",
        lambda *a, **k: _FakeStreamResp([b"%PDF-1.4 fake ", b"content"]),
    )
    dest = tmp_path / "a.pdf"
    res = download_file("https://oa.example.com/a.pdf", dest)
    assert res.ok and res.data == len(b"%PDF-1.4 fake content")
    assert dest.read_bytes() == b"%PDF-1.4 fake content"
    assert not (tmp_path / "a.pdf.part").exists()


def test_download_file_oversize(tmp_path, monkeypatch):
    """超 max_bytes 中断：归一「文件过大」+ 不落成品/半成品。"""
    monkeypatch.setattr(
        "pipeline_core.http_client.httpx.stream",
        lambda *a, **k: _FakeStreamResp([b"x" * 100, b"y" * 100]),
    )
    dest = tmp_path / "big.pdf"
    res = download_file("https://oa.example.com/big.pdf", dest, max_bytes=150)
    assert not res.ok and "文件过大" in res.error
    assert not dest.exists() and not (tmp_path / "big.pdf.part").exists()


def test_download_file_http_error(tmp_path, monkeypatch):
    """HTTP 403 → 归一错误，不落文件。"""
    monkeypatch.setattr(
        "pipeline_core.http_client.httpx.stream",
        lambda *a, **k: _FakeStreamResp([], status=403),
    )
    dest = tmp_path / "x.pdf"
    res = download_file("https://oa.example.com/x.pdf", dest)
    assert not res.ok and "403" in res.error and not dest.exists()


# ---- /web/oa_ingest 端点 ----

_REQ = {
    "url": "https://oa.example.com/paper.pdf",
    "title": "乡村振兴的治理路径",
    "authors": ["测试作者"],
    "journal": "测试期刊",
    "year": "2024",
    "doi": "10.9999/test.1",
    "source": "DOAJ",
    "search_date": "2026-08-12",
}


@pytest.fixture
def client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}, tmp_path


def _fake_download_ok(pdf_bytes):
    def fake(url, dest, *, timeout=60, max_bytes=0, headers=None, redact=None):
        dest.write_bytes(pdf_bytes)
        return NetResult(ok=True, status=200, data=len(pdf_bytes), error="", elapsed=0.1)
    return fake


def test_oa_ingest_success(client, tmp_path, monkeypatch):
    """A/C 2/3：下载→校验→提取→03 队列 + sidecar 来源声明 + 原件归档 + tmp 清理。"""
    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf, "rural revitalization governance path")  # fitz 内置字体无中文字形，用英文文本层
    monkeypatch.setattr(
        "pipeline_core.http_client.download_file", _fake_download_ok(pdf.read_bytes())
    )
    c, h, root = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    data = r.json()
    assert r.status_code == 200 and data["ok"] and data["ingested"] and data["queued"]
    assert data["source"] == "DOAJ" and data["search_date"] == "2026-08-12"

    doc_id = data["doc_id"]
    # sidecar：bibliography 含元数据 + 来源声明（A/C 3）
    import json

    sc = json.loads((root / "sidecar" / f"{doc_id}.json").read_text(encoding="utf-8"))
    bib = sc["bibliography"]
    assert bib["title"] == "乡村振兴的治理路径" and bib["origin"] == "oa_download"
    assert bib["oa_url"] == _REQ["url"] and bib["oa_source"] == "DOAJ"
    assert sc["page_count"] == 1
    # md 落 03 队列且 stem 与 source_file 对齐（M4 匹配前提）
    stem = sc["source_file"].removesuffix(".pdf")
    md = root / "03_知识库原文" / f"{stem}.md"
    md_text = md.read_text(encoding="utf-8")
    assert md.exists() and "rural revitalization" in md_text
    assert md_text.startswith("<!-- page 1 -->")  # 页码标注（速览卡页跳转锚点，对齐 M2 格式）
    # 原件归档 04 + tmp 清理（A/C 4 无半成品）
    assert list((root / "04_已入库归档").glob("*/原始文件/*.pdf"))
    assert not list((root / ".upload_tmp").glob("*"))


def test_oa_ingest_duplicate(client, tmp_path, monkeypatch):
    """同内容二次下载 → duplicate，不重复入库。"""
    pdf = tmp_path / "src.pdf"
    _make_pdf(pdf, "判重测试内容")
    monkeypatch.setattr(
        "pipeline_core.http_client.download_file", _fake_download_ok(pdf.read_bytes())
    )
    c, h, root = client
    r1 = c.post("/web/oa_ingest", json=_REQ, headers=h).json()
    r2 = c.post("/web/oa_ingest", json=_REQ, headers=h).json()
    assert r1["ingested"] and not r1.get("duplicate")
    assert r2.get("duplicate") is True and not r2["ingested"]
    # 03 队列里只有一个 md
    assert len(list((root / "03_知识库原文").glob("*.md"))) == 1


def test_oa_ingest_non_pdf_rejected(client, tmp_path, monkeypatch):
    """A/C 4：落地页无 PDF 候选 → 422 + 无半成品（无 sidecar/md/归档）。"""
    monkeypatch.setattr(
        "pipeline_core.http_client.download_file", _fake_download_ok(b"<html>login required</html>")
    )
    c, h, root = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    assert r.status_code == 422 and "落地页" in r.json()["error"]
    assert not list((root / "sidecar").glob("*.json"))
    assert not list((root / ".upload_tmp").glob("*"))


def test_oa_ingest_landing_page_rescued(client, tmp_path, monkeypatch):
    """落地页 HTML 内发现 PDF 直链 → 二次下载入库成功（DOAJ 中文刊形态）。"""
    pdf = tmp_path / "real.pdf"
    _make_pdf(pdf, "rescued pdf content")
    landing = b'<html><body><a href="CN/article/downloadArticleFile.do?attachType=PDF&id=123">PDF</a></body></html>'

    def fake_download(url, dest, **k):
        if "downloadArticleFile" in url:
            dest.write_bytes(pdf.read_bytes())
            return NetResult(ok=True, status=200, data=pdf.stat().st_size, error="", elapsed=0.1)
        dest.write_bytes(landing)
        return NetResult(ok=True, status=200, data=len(landing), error="", elapsed=0.1)

    monkeypatch.setattr("pipeline_core.http_client.download_file", fake_download)
    c, h, root = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    data = r.json()
    assert r.status_code == 200 and data["ok"] and data["ingested"]
    doc_id = data["doc_id"]
    assert (root / "sidecar" / f"{doc_id}.json").exists()


def test_extract_pdf_candidates():
    """落地页解析：.pdf 后缀 / download+pdf 组合；相对链接绝对化；去重保序；非 PDF 剔除。"""
    from m7_websearch.oa_pdf import extract_pdf_candidates, looks_like_html

    base = "https://www.rddl.com.cn/CN/10.13284/j.cnki.rddl.20230678"
    html = """
    <html><head>
      <meta name="citation_pdf_url" content="https://www.rddl.com.cn/CN/PDF/10.13284/j.cnki.rddl.20230678"/>
    </head><body>
      <a href="fileup/1007-7588/PDF/1782436356682.pdf">下载PDF</a>
      <a href="/CN/article/downloadArticleFile.do?attachType=PDF&id=123">PDF下载</a>
      <a href="fileup/1007-7588/PDF/1782436356682.pdf">重复链接</a>
      <a href="https://other.com/about.html">关于</a>
      <a href="#top">锚点</a>
    </body></html>
    """.encode("utf-8")
    assert looks_like_html(html)
    cands = extract_pdf_candidates(html, base)
    assert cands == [
        # citation_pdf_url meta 优先级最高（Google Scholar 标准，权威直链）
        "https://www.rddl.com.cn/CN/PDF/10.13284/j.cnki.rddl.20230678",
        "https://www.rddl.com.cn/CN/10.13284/fileup/1007-7588/PDF/1782436356682.pdf",
        "https://www.rddl.com.cn/CN/article/downloadArticleFile.do?attachType=PDF&id=123",
    ]  # 相对链接 urljoin 绝对化；重复剔除；about/锚点不入候选
    assert not looks_like_html(b"%PDF-1.4 binary")


def test_oa_ingest_scan_pdf_rejected(client, tmp_path, monkeypatch):
    """A/C 4：扫描件（无文本层）422 + 无半成品。"""
    pdf = tmp_path / "scan.pdf"
    _make_pdf(pdf, "")  # 无文本层
    monkeypatch.setattr(
        "pipeline_core.http_client.download_file", _fake_download_ok(pdf.read_bytes())
    )
    c, h, root = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    assert r.status_code == 422 and "扫描件" in r.json()["error"]
    assert not list((root / "sidecar").glob("*.json"))


def test_oa_ingest_download_fail(client, monkeypatch):
    """A/C 4：下载失败（链接失效）502 明确错误。"""
    monkeypatch.setattr(
        "pipeline_core.http_client.download_file",
        lambda url, dest, **k: NetResult(ok=False, status=404, data=None, error="HTTP 错误 404", elapsed=0.1),
    )
    c, h, _ = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    assert r.status_code == 502 and "下载失败" in r.json()["error"]


def test_oa_ingest_disabled_when_off(client, monkeypatch):
    """完全本地模式：下载入库整体不可用（同检索门控）。"""
    monkeypatch.setattr("pipeline_core.http_client.web_search_enabled", lambda: False)
    c, h, _ = client
    r = c.post("/web/oa_ingest", json=_REQ, headers=h)
    assert r.json()["ok"] is False and r.json()["enabled"] is False
