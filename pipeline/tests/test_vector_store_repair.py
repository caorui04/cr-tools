"""chunks_vec 孤儿自愈机制测试（T1 教训固化：vec 残留致入库 UNIQUE 冲突）。

覆盖：① init_db 自动清理孤儿（历史 delete_doc 只删 chunks 的残留）；② 自愈后
insert_chunks 不再因孤儿 rowid 冲突而失败（入库事务正常完成）。
"""

import json
import sqlite3

from m4_vectordb.vector_store import init_db, insert_chunks

DIM = 1024
ZERO_VEC = [0.0] * DIM


def _connect(db_path):
    import sqlite_vec
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def _make_orphan(db_path):
    """制造孤儿：手动插入 chunk + vec 后只删 chunk（模拟旧 delete_doc 只删 chunks）。"""
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO chunks (chunk_id, doc_hash, seq, text, metadata_json, created_at) "
        "VALUES ('orphan_0', 'orphan', 0, 't', '{}', '2026-01-01')"
    )
    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)", (rowid, json.dumps(ZERO_VEC)))
    conn.execute("DELETE FROM chunks WHERE chunk_id = 'orphan_0'")  # 只删 chunks → vec 成孤儿
    conn.commit()
    assert conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 1
    conn.close()


def test_init_db_repairs_orphan_vec_rows(tmp_path):
    db = tmp_path / "kb.db"
    init_db(db)
    _make_orphan(db)
    # 自愈：init_db（入库/检索都会经过）应清掉孤儿
    init_db(db)
    conn = _connect(db)
    assert conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 0
    conn.close()


def test_insert_succeeds_after_orphan_repair(tmp_path):
    db = tmp_path / "kb.db"
    init_db(db)
    _make_orphan(db)
    init_db(db)  # 自愈

    class C:
        def __init__(self, seq, text):
            self.seq, self.text = seq, text

    # 修复后入库不再撞孤儿 rowid（修复前 UNIQUE constraint failed → 事务回滚）
    n = insert_chunks(db, "doc1", [C(0, "hello")], [ZERO_VEC], [{"source_file": "a.txt"}])
    assert n == 1
    conn = _connect(db)
    assert conn.execute("SELECT count(*) FROM chunks WHERE doc_hash='doc1'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 1
    conn.close()
