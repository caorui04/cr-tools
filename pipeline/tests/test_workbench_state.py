"""workbench_state 单测（U1 路径跨边界契约第 6 节登记，批次 F）。

覆盖契约要求的三个点，全部用 tmp_path，不依赖真实管线数据：
(a) ensure 幂等——二次调用不改动已有固化（paths/filemap/created_at）；
(b) 物理名碰撞分支（workbench_state.py _rename_to 的 target.exists() 兜底）；
(c) phys_path / logical_name 往返一致。
"""

import logging

import pytest

from server import workbench_state as ws


def _make_paper(tmp_path):
    paper_dir = tmp_path / "papers" / "p_test_0001"
    paper_dir.mkdir(parents=True)
    return paper_dir


def test_ensure_idempotent(tmp_path):
    """(a) ensure 幂等：二次调用不改动已有固化，也不重复迁移已映射文件。"""
    paper_dir = _make_paper(tmp_path)
    drafts = paper_dir / "drafts"
    drafts.mkdir()
    (drafts / "笔记.md").write_text("草稿内容", encoding="utf-8")

    st1 = ws.ensure(paper_dir)
    # 首次 ensure：旧格式原名迁移到物理名
    assert "drafts/笔记.md" in st1["filemap"]
    phys_rel = st1["filemap"]["drafts/笔记.md"]
    assert (paper_dir / phys_rel).is_file()
    assert not (drafts / "笔记.md").exists()

    st2 = ws.ensure(paper_dir)
    # 二次调用：固化结果逐项一致（last_updated 同秒不变，跨秒也只动时间戳）
    assert st2["paths"] == st1["paths"]
    assert st2["filemap"] == st1["filemap"]
    assert st2["created_at"] == st1["created_at"]
    # 物理文件未被二次改名
    assert (paper_dir / phys_rel).is_file()
    assert (paper_dir / phys_rel).read_text(encoding="utf-8") == "草稿内容"


def test_physical_name_collision(tmp_path, caplog):
    """(b) 物理名碰撞：目标物理名已被占用时 _rename_to 加序号保底（_1 后缀）。"""
    paper_dir = _make_paper(tmp_path)
    drafts = paper_dir / "drafts"
    drafts.mkdir()
    # 待迁移的旧格式文件
    (drafts / "foo.md").write_text("内容A", encoding="utf-8")
    # 预先占用 foo.md 的确定性物理名（内容不同的另一个文件）
    phys_rel = ws.physical_name("drafts/foo.md")  # drafts/foo_<hash>.md
    phys_name = phys_rel.rsplit("/", 1)[1]
    (drafts / phys_name).write_text("内容B", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        st = ws.ensure(paper_dir)

    # 碰撞分支触发：foo.md 被改名到 <phys_stem>_1.md，占用者原样保留
    stem, ext = phys_name.rsplit(".", 1)
    collided = drafts / f"{stem}_1.{ext}"
    assert collided.is_file()
    assert collided.read_text(encoding="utf-8") == "内容A"
    assert (drafts / phys_name).read_text(encoding="utf-8") == "内容B"
    assert not (drafts / "foo.md").exists()
    assert any("物理名碰撞" in r.message for r in caplog.records)
    # filemap 登记逻辑名 → 原确定性物理名（现状行为，如实断言）
    assert st["filemap"]["drafts/foo.md"] == phys_rel


def test_phys_path_logical_name_roundtrip(tmp_path):
    """(c) register → phys_path / logical_name 往返一致（含中文逻辑名）。"""
    paper_dir = _make_paper(tmp_path)
    st = ws.ensure(paper_dir)

    logical_rel = "drafts/妇女史论文.md"
    p = ws.register(paper_dir, st, logical_rel)
    phys_rel = st["filemap"][logical_rel]

    # register 返回值 == phys_path 解析值 == paper_dir / 物理相对路径
    assert p == paper_dir / phys_rel
    assert ws.phys_path(paper_dir, st, logical_rel) == paper_dir / phys_rel
    # 物理名反查逻辑名
    assert ws.logical_name(paper_dir, st, phys_rel) == logical_rel
    # 未映射的逻辑名：phys_path 抛 FileNotFoundError；未映射的物理名：按原名返回
    with pytest.raises(FileNotFoundError):
        ws.phys_path(paper_dir, st, "drafts/不存在.md")
    assert ws.logical_name(paper_dir, st, "drafts/未映射_xyz.md") == "drafts/未映射_xyz.md"


def test_ensure_concurrent_threads(tmp_path):
    """并发 ensure/load 回归（2026-08-10 paper_detail 500 竞态）。

    uvicorn 线程池下并发 paper_detail → 各触发 ensure 写同一 workbench-state.json，
    os.replace 撞 Python 读句柄（无 FILE_SHARE_DELETE）即 WinError 5。
    修复：模块级 RLock 串行化全部读写 + atomic_write 退避重试。
    """
    import json
    import threading

    from server import workbench_state as ws

    paper_dir = tmp_path / "p_concurrent"
    (paper_dir / "drafts").mkdir(parents=True)
    (paper_dir / "drafts" / "中文草稿.md").write_text("# 内容", encoding="utf-8")

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(15):
                st = ws.ensure(paper_dir)
                ws.load(paper_dir)
                assert st["paths"]["drafts"]
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"并发 ensure 出错: {errors[:3]}"
    final = json.loads((paper_dir / "workbench-state.json").read_text(encoding="utf-8"))
    assert final["filemap"], "filemap 应已迁移建立"
