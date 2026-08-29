"""对外检索接口（供 P5 /search 路由调用）。"""

from __future__ import annotations

from pathlib import Path

from .embedder import Embedder
from .vector_store import vector_search


def search(
    root: Path,
    embedder: Embedder,
    query: str,
    top_k: int = 5,
    doc_hash: str | None = None,
) -> list[dict]:
    """向量检索入口。返回原始字段列表，由 P5 组装 HTTP 响应。

    返回字段：chunk_id, doc_hash, text, score(=1-distance),
    doc_meta(含 source_file/title/page_estimate)。
    """
    query_vec = embedder.encode_query(query)
    if query_vec is None:
        # 嵌入服务不可用：明确报错而非静默空/假匹配（防"检索不出资料"）
        raise RuntimeError("嵌入模型不可用，请先在模型面板启动 BGE-M3（端口 8083）")
    db_path = root / "vector_db" / "kb.db"

    results = vector_search(db_path, query_vec, top_k, doc_hash)
    return [
        {
            "chunk_id": r["chunk_id"],
            "doc_hash": r["doc_hash"],
            "text": r["text"],
            "score": r["score"],
            "doc_meta": {
                "source_file": r["metadata_json"].get("source_file", ""),
                "title": r["metadata_json"].get("title"),
                "page_estimate": r["metadata_json"].get("page_start"),
            },
        }
        for r in results
    ]
