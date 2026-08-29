"""sqlite-vec 表操作。

表结构见 C4 §1。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _connect(db_path: Path):
    """建连并加载 sqlite-vec 扩展（所有用到 vec0 的连接必须走这里）。"""
    import sqlite_vec
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def init_db(db_path: Path) -> None:
    """初始化 kb.db：创建 chunks 表和 chunks_vec 虚拟表。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id   TEXT PRIMARY KEY,
            doc_hash   TEXT NOT NULL,
            seq        INTEGER NOT NULL,
            text       TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    # sqlite-vec 虚拟表（扩展已在 _connect 加载；失败应显式报错而非静默跳过）
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            embedding float[1024]
        )
    """)
    # 自愈（T1 教训固化）：清理 chunks_vec 孤儿行——历史 delete_doc 只删 chunks
    # 的残留、异常中断都可能产生；孤儿会与新插入 chunk 的自增 rowid 冲突 →
    # UNIQUE 失败 → 入库事务整体回滚（文件滞留 03 队列反复失败）。
    # init_db 被入库（index_document）与检索（vector_search）两条路径都调用，
    # 任何一次库操作都触发自愈，无死角且零人工。
    repair_orphan_vec_rows(conn)
    conn.commit()
    conn.close()


def repair_orphan_vec_rows(conn) -> int:
    """自愈：清理 chunks_vec 中 rowid 不在 chunks 表的孤儿向量行。返回清理行数。

    幂等（无孤儿删除 0 行）；自愈失败只记日志不抛——防御性操作不得阻塞
    正常建表/查询主流程（由下一次库操作再试）。
    """
    try:
        return conn.execute(
            "DELETE FROM chunks_vec WHERE rowid NOT IN (SELECT rowid FROM chunks)"
        ).rowcount
    except Exception as e:
        logger.warning(f"chunks_vec 孤儿自愈失败（不阻塞主流程，下次库操作重试）: {e}")
        return 0


def insert_chunks(
    db_path: Path,
    doc_hash: str,
    chunks: list,
    embeddings: list[list[float]],
    metadata_list: list[dict],
) -> int:
    """事务内：先 DELETE 同 doc_hash 旧记录，再批量插入。"""
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    try:
        conn.execute("BEGIN")
        # 先取出该文档的 rowid，再按 rowid 精确删除 chunks 与 vec（避免误删其他文档的向量行）
        old_rowids = [r[0] for r in conn.execute("SELECT rowid FROM chunks WHERE doc_hash = ?", (doc_hash,)).fetchall()]
        conn.execute("DELETE FROM chunks WHERE doc_hash = ?", (doc_hash,))
        if old_rowids:
            conn.execute(
                f"DELETE FROM chunks_vec WHERE rowid IN ({','.join('?' * len(old_rowids))})",
                old_rowids,
            )

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_hash}_{chunk.seq}"
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    doc_hash,
                    chunk.seq,
                    chunk.text,
                    json.dumps(metadata_list[i], ensure_ascii=False),
                    now,
                ),
            )
            rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            emb = embeddings[i]
            conn.execute(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, json.dumps(emb)),
            )

        conn.execute("COMMIT")
        return len(chunks)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def vector_search(
    db_path: Path,
    query_vec: list[float],
    top_k: int = 5,
    doc_hash: str | None = None,
) -> list[dict]:
    """向量检索。返回 [{"chunk_id", "doc_hash", "text", "score", ...}]"""
    init_db(db_path)  # 幂等；空库/新库下保证表存在（查询返回空而非报错）
    conn = _connect(db_path)
    emb_json = json.dumps(query_vec)

    if doc_hash:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.doc_hash, c.text, c.metadata_json, v.distance
            FROM chunks_vec v
            JOIN chunks c ON c.rowid = v.rowid
            WHERE v.embedding MATCH ? AND c.doc_hash = ? AND k = ?
            ORDER BY v.distance
            """,
            (emb_json, doc_hash, top_k),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.doc_hash, c.text, c.metadata_json, v.distance
            FROM chunks_vec v
            JOIN chunks c ON c.rowid = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (emb_json, top_k),
        ).fetchall()

    conn.close()
    return [
        {
            "chunk_id": r[0],
            "doc_hash": r[1],
            "text": r[2],
            "metadata_json": json.loads(r[3]),
            "score": round(1.0 - r[4], 4),
        }
        for r in rows
    ]


def delete_doc(db_path: Path, doc_hash: str) -> int:
    """删除文档全部 chunk（含向量行，对齐 insert_chunks 的 rowid 精确删除模式）。

    先取该文档全部 rowid，再按 rowid 精确删 chunks_vec，最后删 chunks——
    只删 chunks 会让 vec 表残留孤儿行（空间泄漏；T1 删除联动依赖本函数干净移除）。
    """
    conn = _connect(db_path)
    old_rowids = [r[0] for r in conn.execute("SELECT rowid FROM chunks WHERE doc_hash = ?", (doc_hash,)).fetchall()]
    cursor = conn.execute("DELETE FROM chunks WHERE doc_hash = ?", (doc_hash,))
    deleted = cursor.rowcount
    if old_rowids:
        conn.execute(
            f"DELETE FROM chunks_vec WHERE rowid IN ({','.join('?' * len(old_rowids))})",
            old_rowids,
        )
    conn.commit()
    conn.close()
    return deleted


def get_chunks_total(db_path: Path) -> int:
    """返回 chunks 表总行数。"""
    conn = _connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    return count
