"""bibliography_store.py 单测（D8，批次 P）。

覆盖清单纯逻辑（routes 端点薄壳化的行为等价依据）：
(a) 加载/保存经 U1 权威路径（workbench-state paths.biblio），空骨架缺省值；
(b) add：seq 顺序分配 + doc_id 判重（find_by_doc_id）+ 可选元数据截断登记；
(c) remove：按 seq 移除 + 全量重排，未找到返回 False；
(d) set_format：格式字段切换 + 全量重渲染（经 cite_skill.render_formatted）。
"""

import json

from m6_cite import bibliography_store as store
from server import workbench_state as ws


def _draft(title: str) -> dict:
    return {
        "draft": {
            "authors": ["张三"], "title": title, "type": "J",
            "venue": "测试学报", "year": "2024", "volume": "1",
            "issue": "2", "pages": "10-20", "doi": None,
            "publisher": None, "url": None,
        },
        "formatted": f"张三. {title}[J]. 测试学报. 2024, 1(2): 10-20.",
        "confirmed": False,
    }


def test_biblio_path_via_workbench_state(tmp_path):
    """(a) 清单路径取 workbench-state 固化的 paths.biblio（U1 权威路径）。"""
    d = tmp_path / "papers" / "p_t001"
    d.mkdir(parents=True)
    p = store.biblio_path(d)
    assert p == d / "bibliography.csl.json"
    st = ws.load(d)
    assert st["paths"]["biblio"] == str(p)


def test_load_empty_skeleton(tmp_path):
    """(a 补) 清单缺失时返回空骨架：paper_id=目录名、默认格式、空 items。"""
    d = tmp_path / "p_t002"
    d.mkdir()
    data = store.load(d)
    assert data == {"paper_id": "p_t002", "format": "GB/T 7714-2015", "items": []}


def test_load_corrupt_returns_skeleton(tmp_path):
    """(a 补) 清单损坏/结构不符时回退空骨架，不抛。"""
    d = tmp_path / "p_t003"
    d.mkdir()
    store.biblio_path(d).write_text("{bad json", encoding="utf-8")
    assert store.load(d)["items"] == []
    store.biblio_path(d).write_text('{"items": "not-a-list"}', encoding="utf-8")
    assert store.load(d)["items"] == []


def test_save_roundtrip_and_updated_at(tmp_path):
    """(a 补) save 刷新 updated_at 并落盘，load 读回一致。"""
    d = tmp_path / "p_t004"
    d.mkdir()
    data = store.load(d)
    store.add_item(data, "doc_a", _draft("文甲"))
    store.save(d, data)
    assert data["updated_at"]  # 已刷新
    raw = json.loads(store.biblio_path(d).read_text(encoding="utf-8"))
    assert raw["items"][0]["doc_id"] == "doc_a"
    reloaded = store.load(d)
    assert reloaded["items"] == data["items"]


def test_add_seq_and_dedupe_lookup(tmp_path):
    """(b) seq 按顺序分配；find_by_doc_id 命中即判重（端点据此回 ok:False + seq）。"""
    d = tmp_path / "p_t005"
    d.mkdir()
    data = store.load(d)
    i1 = store.add_item(data, "doc_a", _draft("文甲"))
    i2 = store.add_item(data, "doc_b", _draft("文乙"))
    assert (i1["seq"], i2["seq"]) == (1, 2)
    dup = store.find_by_doc_id(data, "doc_a")
    assert dup is not None and dup["seq"] == 1
    assert store.find_by_doc_id(data, "doc_zzz") is None


def test_add_optional_metadata_trimmed(tmp_path):
    """(b 补) page/snippet/draft_id/anchor 可选元数据：合法值登记 + 截断，非法值忽略。"""
    d = tmp_path / "p_t006"
    d.mkdir()
    data = store.load(d)
    item = store.add_item(
        data, "doc_a", _draft("文甲"),
        page=7, snippet="  " + "长" * 600, draft_id="d1", anchor=" 锚点 ",
    )
    assert item["page"] == 7
    assert len(item["snippet"]) == 500
    assert item["draft_id"] == "d1"
    assert item["anchor"] == "锚点"
    # 非法值不登记
    item2 = store.add_item(data, "doc_b", _draft("文乙"), page=-1, snippet="   ")
    assert "page" not in item2 and "snippet" not in item2


def test_remove_reseq_and_missing(tmp_path):
    """(c) 移除中间条目后全量重排（编号=顺序）；未找到返回 False。"""
    d = tmp_path / "p_t007"
    d.mkdir()
    data = store.load(d)
    for doc in ("doc_a", "doc_b", "doc_c"):
        store.add_item(data, doc, _draft(doc))
    assert store.remove_item(data, 2) is True
    seqs = [(it["doc_id"], it["seq"]) for it in data["items"]]
    assert seqs == [("doc_a", 1), ("doc_c", 2)]
    assert store.remove_item(data, 99) is False
    assert len(data["items"]) == 2


def test_set_format_rerenders(tmp_path):
    """(d) 切格式：format 字段更新 + 全量重渲染（GB/T 与 APA 输出形态不同）。"""
    d = tmp_path / "p_t008"
    d.mkdir()
    data = store.load(d)
    store.add_item(data, "doc_a", _draft("文甲"))
    gbt = data["items"][0]["formatted"]
    store.set_format(data, "APA")
    assert data["format"] == "APA"
    apa = data["items"][0]["formatted"]
    assert apa != gbt
    assert "(2024)" in apa  # APA 形态：作者 (年份)
    # bibliography 缺失的条目不重渲染、不报错
    data["items"].append({"seq": 2, "doc_id": "doc_x", "bibliography": None})
    store.set_format(data, "MLA")
    assert data["items"][1].get("formatted") is None
