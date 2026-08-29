"""POST /papers/{id}/inputs/{name}/index 端点测试（T1 参考资料入语义检索库）。

覆盖：md/txt 入库排队（03 md + sidecar + inputs_log doc_id 回写）、
扫描件 PDF 拒绝、图片拒绝（第九/二十条口径只预览不 ingest）、
同内容判重（A/C 4）、删除联动移除检索库文件（A/C 2）、GBK 编码解码（6.3 纪律）。

注：完整向量化入库依赖 M4 watcher + 嵌入服务（真实环境运行），端点职责止于
「提取文本写 03 队列 + 建 sidecar + 回写关联」；测试验证该职责与判重/删除联动。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from pipeline_core.doc_id import compute_doc_id
from server import workbench_state as ws
from server.routes import create_app

PAPER = "p_t300"


def _client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def _mk_paper(tmp_path, files=("a.txt",)):
    d = tmp_path / "papers" / PAPER / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    for name in files:
        (d / name).write_text("内容", encoding="utf-8")
    # 回填 inputs_log（真实运行时 pt_upload_input 已登记；测试模拟）
    st = ws.ensure(d.parent)
    ws.ensure_inputs_log(d.parent, st)
    return d


def _doc_id_of(d, name):
    return compute_doc_id(d / name)


def test_index_txt_queues_and_links_doc_id(tmp_path):
    d = _mk_paper(tmp_path, ("a.txt",))
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/a.txt/index", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["indexed"] is True and body["queued"] is True
    doc_id = body["doc_id"]
    # 03_知识库原文/ 生成原名 stem md（M4 watcher 消费）
    assert (tmp_path / "03_知识库原文" / "a.md").exists()
    # sidecar 已建（doc_id 判重锚点）
    assert (tmp_path / "sidecar" / f"{doc_id}.json").exists()
    # inputs_log 回写 doc_id（/search 徽标对照）
    st = ws.load(tmp_path / "papers" / PAPER)
    assert any(x["name"] == "a.txt" and x.get("doc_id") == doc_id for x in st["inputs_log"])


def test_index_pdf_scanned_rejected(tmp_path):
    import fitz
    d = tmp_path / "papers" / PAPER / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    pdf = d / "扫件.pdf"
    doc = fitz.open()
    doc.new_page()  # 空白页 = 无文本层（扫描件等效）
    doc.save(str(pdf))
    doc.close()
    st = ws.ensure(d.parent)
    ws.ensure_inputs_log(d.parent, st)
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/扫件.pdf/index", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["indexed"] is False
    assert "扫描件" in body["reason"]


def test_index_image_rejected(tmp_path):
    d = _mk_paper(tmp_path, ("pic.png",))
    # 覆写为假图片字节（扩展名驱动，端点只按扩展名拒绝，不看内容）
    (d / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/pic.png/index", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "只预览不接 ingest" in body["reason"]
    # 未生成任何 md
    assert not (tmp_path / "03_知识库原文").exists() or not list((tmp_path / "03_知识库原文").glob("*.md"))


def test_index_duplicate_same_content(tmp_path):
    d = _mk_paper(tmp_path, ("a.txt", "a_dup.txt"))
    (d / "a_dup.txt").write_text("内容", encoding="utf-8")  # 同内容
    client, headers = _client(tmp_path)
    r1 = client.post(f"/papers/{PAPER}/inputs/a.txt/index", headers=headers)
    assert r1.json()["indexed"] is True
    doc_id = r1.json()["doc_id"]
    r2 = client.post(f"/papers/{PAPER}/inputs/a_dup.txt/index", headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["ok"] is True and body["indexed"] is False and body["duplicate"] is True
    assert body["doc_id"] == doc_id
    # 同内容同 doc_id：sidecar 唯一、03 只有一个 md（判重不重复入库）
    sidecars = list((tmp_path / "sidecar").glob(f"{doc_id}.json"))
    assert len(sidecars) == 1
    mds = list((tmp_path / "03_知识库原文").glob("*.md"))
    assert len(mds) == 1
    # inputs_log 两行都回写同一 doc_id
    st = ws.load(tmp_path / "papers" / PAPER)
    assert all(x.get("doc_id") == doc_id for x in st["inputs_log"] if x["name"] in ("a.txt", "a_dup.txt"))


def test_delete_removes_kb_files(tmp_path):
    d = _mk_paper(tmp_path, ("a.txt",))
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/a.txt/index", headers=headers)
    doc_id = r.json()["doc_id"]
    assert (tmp_path / "03_知识库原文" / "a.md").exists()
    assert (tmp_path / "sidecar" / f"{doc_id}.json").exists()
    # 删除 → 检索库文件联动移除（A/C 2）
    r = client.delete(f"/papers/{PAPER}/inputs/a.txt", headers=headers)
    assert r.status_code == 200
    assert not (tmp_path / "03_知识库原文" / "a.md").exists()
    assert not (tmp_path / "sidecar" / f"{doc_id}.json").exists()
    st = ws.load(tmp_path / "papers" / PAPER)
    assert not any(x["name"] == "a.txt" for x in st.get("inputs_log", []))
    # 文件已进 _trash（回收站语义）
    trashed = list((tmp_path / "papers" / PAPER / "inputs" / "_trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.endswith("_a.txt")


def test_index_gbk_txt_decodes(tmp_path):
    d = tmp_path / "papers" / PAPER / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "中文.txt").write_bytes("中文内容测试".encode("gbk"))  # Excel 中文导出常见编码
    st = ws.ensure(d.parent)
    ws.ensure_inputs_log(d.parent, st)
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/中文.txt/index", headers=headers)
    assert r.json()["indexed"] is True
    md = (tmp_path / "03_知识库原文" / "中文.md").read_text(encoding="utf-8")
    assert "中文内容测试" in md  # GBK 正确解码，非乱码


def test_index_bad_docx_returns_reason(tmp_path):
    d = _mk_paper(tmp_path, ("bad.docx",))
    (d / "bad.docx").write_text("不是 docx", encoding="utf-8")  # 假 docx → pandoc 失败
    client, headers = _client(tmp_path)
    r = client.post(f"/papers/{PAPER}/inputs/bad.docx/index", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["indexed"] is False
    assert "pandoc" in body["reason"]
