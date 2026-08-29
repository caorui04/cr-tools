"""GET /papers/{id}/inputs/{name}/preview_table 端点测试（xlsx 只读表格预览，方案 A）。

覆盖：正常读取（首行表头 + 行数/类型转 str）、>500 行截断、非 xlsx 404、
路径穿越 404（U3 pathsafe）、文件缺失 404。tmp_path 造 xlsx 经 openpyxl 写入。
"""

import pytest

openpyxl = pytest.importorskip("openpyxl")

from fastapi.testclient import TestClient

from pipeline_core.config import PipelineConfig
from server.routes import create_app

PAPER = "p_t100"


def _client(tmp_path):
    cfg = PipelineConfig(pipeline_root=tmp_path)
    app = create_app(cfg)
    token = (tmp_path / ".api_token").read_text(encoding="utf-8").strip()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def _mk_xlsx(tmp_path, rows, name="表.xlsx"):
    d = tmp_path / "papers" / PAPER / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(d / name)
    return d


def test_preview_table_ok(tmp_path):
    _mk_xlsx(tmp_path, [["名称", "数量"], ["甲", 1], ["乙", 2.5], ["丙", None]])
    client, headers = _client(tmp_path)
    r = client.get(f"/papers/{PAPER}/inputs/表.xlsx/preview_table", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["columns"] == ["名称", "数量"]
    assert data["rows"] == [["甲", "1"], ["乙", "2.5"], ["丙", ""]]
    assert data["total_rows"] == 4
    assert data["truncated"] is False


def test_preview_table_truncated_over_500_rows(tmp_path):
    _mk_xlsx(tmp_path, [["h"]] + [[i] for i in range(600)])
    client, headers = _client(tmp_path)
    r = client.get(f"/papers/{PAPER}/inputs/表.xlsx/preview_table", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["truncated"] is True
    assert data["total_rows"] == 601
    assert len(data["rows"]) == 499  # 端点上限 500 行含表头（columns 占 1 行）


def test_preview_table_non_xlsx_404(tmp_path):
    d = tmp_path / "papers" / PAPER / "inputs"
    d.mkdir(parents=True)
    (d / "表.csv").write_text("a,b", encoding="utf-8")
    client, headers = _client(tmp_path)
    r = client.get(f"/papers/{PAPER}/inputs/表.csv/preview_table", headers=headers)
    assert r.status_code == 404


def test_preview_table_traversal_404(tmp_path):
    _mk_xlsx(tmp_path, [["a"]])
    client, headers = _client(tmp_path)
    r = client.get(f"/papers/{PAPER}/inputs/..%5C..%5Cevil.xlsx/preview_table", headers=headers)
    assert r.status_code == 404


def test_preview_table_missing_404(tmp_path):
    _mk_xlsx(tmp_path, [["a"]])
    client, headers = _client(tmp_path)
    r = client.get(f"/papers/{PAPER}/inputs/不存在.xlsx/preview_table", headers=headers)
    assert r.status_code == 404
