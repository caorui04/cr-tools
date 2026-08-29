"""DELETE /papers/{id}/inputs/{name} 端点测试（回收站语义，P3 不物理删除）。

覆盖：正常删除进 _trash + inputs_log 登记移除、路径穿越 404（U3 pathsafe）、
文件不存在 404、_trash 目录本身及其内文件不可删 404。
"""

from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from server import workbench_state as ws
from server.routes import create_app

PAPER = "p_t200"


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
    return d


def test_delete_moves_to_trash_and_updates_log(tmp_path):
    d = _mk_paper(tmp_path)
    # 预置 inputs_log 登记（ensure_inputs_log 幂等回填）
    st = ws.ensure(d.parent)
    ws.ensure_inputs_log(d.parent, st)
    st = ws.load(d.parent)
    assert any(r["name"] == "a.txt" for r in st["inputs_log"])
    client, headers = _client(tmp_path)
    r = client.delete(f"/papers/{PAPER}/inputs/a.txt", headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 原位置消失，_trash 归档存在（带时间戳前缀）
    assert not (d / "a.txt").exists()
    trashed = list((d / "_trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.endswith("_a.txt")
    # inputs_log 登记已移除
    st = ws.load(d.parent)
    assert not any(r["name"] == "a.txt" for r in st.get("inputs_log", []))


def test_delete_traversal_404(tmp_path):
    _mk_paper(tmp_path)
    client, headers = _client(tmp_path)
    r = client.delete(f"/papers/{PAPER}/inputs/..%5C..%5Cevil.txt", headers=headers)
    assert r.status_code == 404


def test_delete_missing_404(tmp_path):
    _mk_paper(tmp_path)
    client, headers = _client(tmp_path)
    r = client.delete(f"/papers/{PAPER}/inputs/不存在.txt", headers=headers)
    assert r.status_code == 404


def test_delete_trash_dir_and_trashed_file_404(tmp_path):
    d = _mk_paper(tmp_path)
    client, headers = _client(tmp_path)
    # _trash 目录本身（目录 → 404）
    r = client.delete(f"/papers/{PAPER}/inputs/_trash", headers=headers)
    assert r.status_code == 404
    # 已归档进 _trash 的文件不可再经端点触碰
    r = client.delete(f"/papers/{PAPER}/inputs/a.txt", headers=headers)
    assert r.status_code == 200
    trashed = list((d / "_trash").iterdir())[0]
    r = client.delete(f"/papers/{PAPER}/inputs/_trash%2F{trashed.name}", headers=headers)
    assert r.status_code == 404
