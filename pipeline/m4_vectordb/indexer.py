"""M4 主调度器 — 扫描 03_知识库原文/ → 分块 → 编码 → 入库 → manifest。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pipeline_core.atomic import atomic_move
from pipeline_core.manifest import ManifestEntry, append_manifest, find_by_doc_hash
from pipeline_core.progress import report
from pipeline_core.sidecar import read_sidecar

from .chunker import chunk_markdown
from .embedder import GGUFEmbedder
from .metadata import build_metadata
from .vector_store import get_chunks_total, init_db, insert_chunks

logger = logging.getLogger(__name__)


def _report_stage_progress(root: Path, doc_id: str, done: int, total: int) -> None:
    """进度条埋点（fail-safe：进度写入失败不得影响主流程）。"""
    try:
        report("m4", doc_id, done, total, root=root)
    except Exception:
        pass

# 兼容别名：07-27 架构统一定案为 GGUF 编码器
Embedder = GGUFEmbedder


def _archive_md(root: Path, md_path: Path) -> Path | None:
    """入库成功后归档 md 到 04_已入库归档/YYYY-MM/原文md/。

    修复 PIPE-1：03_知识库原文/ 是 M4 的输入队列，入库后必须移走，
    否则 M4 watcher 每 5s 重新入库同一文件，manifest 无限膨胀。
    """
    month_dir = root / "04_已入库归档" / datetime.now().strftime("%Y-%m") / "原文md"
    month_dir.mkdir(parents=True, exist_ok=True)
    try:
        dest = atomic_move(md_path, month_dir)
        logger.info(f"M4 归档: {md_path.name} → {dest}")
        return dest
    except Exception as e:
        logger.warning(f"M4 归档失败（文件将残留队列被再次处理）: {md_path.name}: {e}")
        return None


def _already_indexed(root: Path, doc_hash: str) -> bool:
    """幂等检查：manifest 中该 doc_hash 已有 upsert 记录即视为已入库。"""
    try:
        entry = find_by_doc_hash(root / "manifest.jsonl", doc_hash)
        return entry is not None and entry.op == "upsert"
    except Exception as e:
        logger.warning(f"manifest 幂等检查失败（按未入库处理）: {e}")
        return False


def index_document(
    root: Path, md_path: Path, embedder: Embedder
) -> str | None:
    """单个 MD 文件入库。返回 doc_hash，失败返回 None。

    从 sidecar 读取 doc_id 和元数据，按 C4 §2 分块，C4 §1 入库，
    C1 §7 追加 manifest。
    """
    # 从 sidecar 精确查找 doc_id：逐个匹配，优先 source_file 精确匹配
    sidecar_dir = root / "sidecar"
    doc_hash = None
    sidecar = {}
    md_stem = md_path.stem

    if sidecar_dir.exists():
        for scf in sorted(sidecar_dir.glob("*.json")):
            try:
                sc = read_sidecar(sidecar_dir, scf.stem)
                src_stem = Path(sc.source_file).stem
                # 精确匹配：MD 文件名以 source_file stem 开头
                # （.ocr.md / .bilingual.md 保留 source stem 作为前缀）
                if md_stem.startswith(src_stem) or md_stem.startswith("[T]" + src_stem):
                    doc_hash = sc.doc_id
                    sidecar = {
                        "source_file": sc.source_file,
                        "low_confidence": sc.low_confidence,
                        "classification": sc.classification,
                        "title": sc.bibliography.get("title") if sc.bibliography else None,
                    }
                    break
            except Exception:
                continue

        # 回退：按 doc_id（12位hex前缀）查找
        if doc_hash is None:
            for scf in sorted(sidecar_dir.glob("*.json")):
                try:
                    if md_stem.startswith(scf.stem):
                        sc = read_sidecar(sidecar_dir, scf.stem)
                        doc_hash = sc.doc_id
                        sidecar = {
                            "source_file": sc.source_file,
                            "low_confidence": sc.low_confidence,
                            "classification": sc.classification,
                            "title": sc.bibliography.get("title") if sc.bibliography else None,
                        }
                        break
                except Exception:
                    continue

    if doc_hash is None:
        # fallback: 从文件名提取（不含扩展名，取前12位或全名）
        doc_hash = md_path.stem[:12] if len(md_path.stem) >= 12 else md_path.stem

    # 幂等检查（PIPE-1）：该 doc_hash 已入库 → 归档移出队列，跳过重新入库
    if _already_indexed(root, doc_hash):
        logger.info(f"M4 幂等跳过: {doc_hash} 已在 manifest（{md_path.name}），归档")
        _archive_md(root, md_path)
        return None

    text = md_path.read_text(encoding="utf-8")
    chunks = chunk_markdown(text)
    if not chunks:
        return None

    # 进度条：开始入库（0 / 总 chunk 数）
    _report_stage_progress(root, doc_hash, 0, len(chunks))

    embeddings = embedder.encode([c.text for c in chunks])
    source_file = sidecar.get("source_file", md_path.name)
    metadata_list = [build_metadata(c, sidecar, source_file) for c in chunks]

    db_path = root / "vector_db" / "kb.db"
    init_db(db_path)
    n = insert_chunks(db_path, doc_hash, chunks, embeddings, metadata_list)

    # 进度条：已入库 chunk / 总 chunk 数
    _report_stage_progress(root, doc_hash, n, len(chunks))

    # 写 manifest（C1 §7）
    chunk_ids = [f"{doc_hash}_{c.seq}" for c in chunks]
    manifest_path = root / "manifest.jsonl"
    append_manifest(
        manifest_path,
        ManifestEntry(
            doc_hash=doc_hash,
            chunk_ids=chunk_ids,
            version=get_chunks_total(db_path),
            ts="",
            op="upsert",
        ),
    )

    logger.info(f"M4 入库完成: {doc_hash} ({n} chunks)")

    # 入库成功 → 归档 md 移出输入队列（PIPE-1：防 watcher 重复入库）
    _archive_md(root, md_path)

    return doc_hash


def index_all_pending(root: Path, embedder: Embedder) -> dict:
    """扫描 03_知识库原文/ 中未入库的 MD，逐文件入库。"""
    kb_dir = root / "03_知识库原文"
    if not kb_dir.exists():
        return {"indexed": 0, "skipped": 0, "errors": []}

    indexed = 0
    skipped = 0
    errors: list[str] = []

    for f in sorted(kb_dir.iterdir()):
        if not f.is_file() or f.suffix != ".md":
            continue
        try:
            result = index_document(root, f, embedder)
            if result:
                indexed += 1
            else:
                skipped += 1
        except Exception as e:
            logger.error(f"入库失败: {f.name}: {e}")
            errors.append(str(e))

    return {"indexed": indexed, "skipped": skipped, "errors": errors}
