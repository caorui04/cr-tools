"""FastAPI 应用入口 + 全部路由。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import json

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pipeline_core.config import PipelineConfig, load_config
from pipeline_core.doc_id import compute_doc_id
from pipeline_core.pandoc import pandoc_convert
from pipeline_core.pathsafe import (
    PathUnsafeError,
    safe_draft_name,
    safe_filename,
    safe_paper_id,
)

from .m0 import compute_safe_filename, ingest_file, validate_file
from . import workbench_state as ws
from .models import (
    BibliographyRequest,
    CiteRequest,
    CiteResponse,
    RetryRequest,
    RetryResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StatusResponse,
    SubmitRequest,
    SubmitResponse,
)

logger = logging.getLogger(__name__)

_app_start_time = time.time()


# ---- 速览卡存储（2026-08-13 用户拍板：升一等数据，纳入资料库管理）----
# 权威目录 digests/<doc_id>.json（清库/环境清理豁免，与向量库同级资产）；
# 旧缓存位 sidecar/<doc_id>_digest.json 仅作读取回退 + 命中即懒迁移（移动不复制）。
def _digest_paths(root: Path, doc_id: str) -> tuple[Path, Path]:
    """返回 (权威位, 旧缓存位)。"""
    return root / "digests" / f"{doc_id}.json", root / "sidecar" / f"{doc_id}_digest.json"


def _digest_read(root: Path, doc_id: str) -> dict | None:
    """读速览卡：权威位优先；旧位命中 → 懒迁移到权威位后返回。无卡返回 None。"""
    new, old = _digest_paths(root, doc_id)
    for p in (new, old):
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue  # 损坏文件视为无卡（重生成覆盖）
        if p == old:
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.replace(new)  # 移动：一等数据只留一份
            except OSError:
                pass  # 迁移失败不影响读取（下次再迁）
        return d
    return None


def _digest_write(root: Path, doc_id: str, card: dict) -> None:
    """写速览卡到权威位，并清旧缓存位副本。"""
    new, old = _digest_paths(root, doc_id)
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink(missing_ok=True)


def _digest_remove(root: Path, doc_id: str) -> None:
    """删除联动：两个位置都清。"""
    for p in _digest_paths(root, doc_id):
        p.unlink(missing_ok=True)


def _check_module_availability():
    """启动时检查各模块可用性，记录警告但不阻塞启动。"""
    modules = {
        "m4_vectordb": ["/search 路由"],
        "m6_cite": ["/cite 路由"],
    }
    for mod_name, affected in modules.items():
        try:
            __import__(mod_name)
            logger.info(f"模块可用: {mod_name}")
        except ImportError:
            logger.warning(
                f"模块不可用: {mod_name}（影响: {', '.join(affected)}），"
                f"相关路由将返回空结果或 stub 降级"
            )


def _is_corrupt_pdf(path: Path) -> bool:
    """E-1：检测 PDF 是否损坏（fitz 打不开）。损坏不拒绝，仅显著警告。"""
    if path.suffix.lower() != ".pdf":
        return False
    try:
        import fitz
        doc = fitz.open(str(path))
        doc.close()
        return False
    except Exception:
        return True


def create_app(config: PipelineConfig | None = None) -> FastAPI:
    """创建 FastAPI app，注册全部路由。"""
    if config is None:
        config = load_config()

    # docs_url 移到 /api-docs：默认 /docs 的 Swagger UI 会与文档列表 API 冲突
    app = FastAPI(title="kb-pipeline", version="1.0", docs_url="/api-docs", redoc_url=None, openapi_url="/api-docs/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8737",
            "http://localhost:8737",
            # M5：宿主（Tauri webview）直连 8737
            # dev 态：tauri dev 页面从 vite 加载，origin = http://localhost:1420
            "http://localhost:1420",
            # 打包态：webview 实际 origin
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.config = config
    app.state.start_time = time.time()

    # Token 鉴权：自动生成随机 token，写入 pipeline_root/.api_token
    # 仅强制要求非 /status 路由（/status 供面板探测用）
    import secrets
    token_path = config.pipeline_root / ".api_token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(16))
    _api_token = token_path.read_text().strip()
    logger.info(f"API token: {_api_token[:8]}... (full token in {token_path})")

    @app.middleware("http")
    async def token_middleware(request: Request, call_next):
        # /status 免鉴权（probe 探测用）；Web UI 页面与静态资源免鉴权
        # （API 路由仍强制 token，防跨站 localhost 调用）
        path = request.url.path
        if (
            path == "/status"
            or path == "/"
            or path == "/favicon.ico"
            or path.startswith(("/css/", "/js/", "/assets/", "/vendor/"))
            or request.method == "OPTIONS"  # CORS 预检（无 Authorization；放行交由 CORSMiddleware 处理）
        ):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        expect = f"Bearer {_api_token}"
        # iframe/embed 发不了 Authorization 头：支持 ?token= 查询参数（供 PDF 预览等 UI 内嵌资源）
        if auth != expect and request.query_params.get("token") != _api_token:
            return JSONResponse(
                status_code=401,
                content={"error": "缺少或无效的 API token。请通过 Authorization: Bearer <token> 访问。"},
            )
        return await call_next(request)

    # 启动时模块可用性检查
    _check_module_availability()

    # P3.5-1 第 4 步：分析后端可插拔——启动时注入 provider（本地优先，降级 stub）
    from llm import providers
    providers.ensure_analysis()

    # D8：CSL 清单 store（清单纯逻辑抽离，端点为薄壳）
    from m6_cite import bibliography_store

    # C3 通用约定：错误统一 {"error": "<msg>"}
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    root = config.pipeline_root

    # ---- POST /submit ----
    @app.post("/submit", response_model=SubmitResponse)
    def submit(req: SubmitRequest):
        accepted = []
        rejected = []
        warnings = []

        for p_str in req.paths:
            p = Path(p_str)
            err = validate_file(p)
            if err:
                rejected.append({"path": p_str, "reason": err})
                continue

            translate_flag = False
            if req.translate_paths:
                translate_flag = p_str in req.translate_paths
            elif req.translate:
                translate_flag = True

            queued_name, doc_id = ingest_file(p, root, translate_flag)
            if doc_id == "duplicate":
                rejected.append({"path": p_str, "reason": "文件已存在"})
                continue

            accepted.append({"path": p_str, "queued_name": queued_name})

            # E-1：损坏 PDF 不拒绝，但显著警告（结果可能不完整）
            if _is_corrupt_pdf(p):
                warnings.append({"path": p_str, "reason": "该文件已损坏，处理结果可能不完整或错误"})
            elif p.suffix.lower() == ".pdf":
                # >500 页警告
                try:
                    import fitz
                    doc = fitz.open(str(p))
                    if len(doc) > config.limits.max_pages_warn:
                        warnings.append({"path": p_str, "reason": f"页数>{config.limits.max_pages_warn}"})
                    doc.close()
                except Exception:
                    pass

        return SubmitResponse(
            accepted=accepted,
            rejected=rejected,
            warnings=warnings if warnings else None,
        )

    # ---- POST /upload（Web UI 上传通道：浏览器只能拿到 File 字节，拿不到磁盘路径）----
    @app.post("/upload")
    async def upload(files: list[UploadFile] = File(...), translate: bool = Form(False)):
        accepted = []
        rejected = []
        warnings = []
        tmp_dir = root / ".upload_tmp"
        tmp_dir.mkdir(exist_ok=True)

        for uf in files:
            fname = uf.filename or "unnamed"
            tmp_path = tmp_dir / fname
            try:
                content = await uf.read()
                tmp_path.write_bytes(content)
            except Exception as e:
                rejected.append({"path": fname, "reason": f"上传写入失败: {e}"})
                continue

            err = validate_file(tmp_path)
            if err:
                rejected.append({"path": fname, "reason": err})
                tmp_path.unlink(missing_ok=True)
                continue

            # E-1：损坏 PDF 不拒绝，但显著警告（结果可能不完整）
            if _is_corrupt_pdf(tmp_path):
                warnings.append({"path": fname, "reason": "该文件已损坏，处理结果可能不完整或错误"})
            elif tmp_path.suffix.lower() == ".pdf":
                # >500 页警告（ingest 前检查）
                try:
                    import fitz
                    doc = fitz.open(str(tmp_path))
                    if len(doc) > config.limits.max_pages_warn:
                        warnings.append({"path": fname, "reason": f"页数>{config.limits.max_pages_warn}"})
                    doc.close()
                except Exception:
                    pass

            queued_name, doc_id = ingest_file(tmp_path, root, translate)
            if doc_id == "duplicate":
                rejected.append({"path": fname, "reason": "文件已存在"})
                tmp_path.unlink(missing_ok=True)
                continue

            accepted.append({"path": fname, "queued_name": queued_name})
            tmp_path.unlink(missing_ok=True)

        return {"accepted": accepted, "rejected": rejected, "warnings": warnings if warnings else None}

    # ---- GET /status ----
    @app.get("/status", response_model=StatusResponse)
    def status():
        queues = {}
        for qname in ["00_待处理", "01_OCR队列", "02_翻译队列", "03_知识库原文", "99_异常"]:
            qdir = root / qname
            if qdir.exists():
                count = 0
                for f in qdir.iterdir():
                    if f.is_file() and f.suffix != ".processing" and not f.name.endswith(".error.txt"):
                        count += 1
                queues[qname] = count
            else:
                queues[qname] = 0

        processing = []
        for qname in ["00_待处理", "01_OCR队列", "02_翻译队列"]:
            qdir = root / qname
            if qdir.exists():
                processing.extend(f.name for f in qdir.glob("*.processing") if f.is_file())

        # E-1：待处理队列文件列表（前端展示；[损坏] 前缀标记损坏文件）
        pending = []
        pending_dir = root / "00_待处理"
        if pending_dir.exists():
            pending = sorted(
                f.name for f in pending_dir.iterdir() if f.is_file()
            )

        manifest_version = 0
        manifest_path = root / "manifest.jsonl"
        if manifest_path.exists():
            manifest_version = sum(1 for _ in open(manifest_path, encoding="utf-8"))

        db_path = root / "vector_db" / "kb.db"
        chunks_total = 0
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                chunks_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                conn.close()
            except Exception:
                pass

        sidecar_dir = root / "sidecar"
        docs_total = len(list(sidecar_dir.glob("*.json"))) if sidecar_dir.exists() else 0

        # 各环节进度条（progress/<stage>.json；缺文件/损坏 → 该环节 null）
        from pipeline_core.progress import read_all
        try:
            stages = read_all(root)
        except Exception:
            stages = {"m1": None, "m2": None, "m3": None, "m4": None}

        return StatusResponse(
            uptime_s=int(time.time() - app.state.start_time),
            queues=queues,
            processing=processing,
            pending=pending,
            manifest_version=manifest_version,
            chunks_total=chunks_total,
            docs_total=docs_total,
            stages=stages,
        )

    # ---- POST /retry ----
    @app.post("/retry", response_model=RetryResponse)
    def retry(req: RetryRequest):
        import os
        from pipeline_core.sidecar import update_sidecar_field
        moved = 0
        err_dir = root / "99_异常"
        pending_dir = root / "00_待处理"

        if req.all:
            for f in list(err_dir.iterdir()):
                if f.is_file() and f.suffix != ".txt" and not f.name.endswith(".error.txt"):
                    dest = pending_dir / f.name
                    os.replace(str(f), str(dest))
                    moved += 1
                    # 查找对应 sidecar 并清零 retry_count（文件名可能被 M1 重命名 ≠ source_file，
                    # 尽力匹配；兜底见下）
                    _reset_sidecar_retry(root, f.name)
            # 兜底：文件名匹配失败（重命名差异）→ 无条件清零全部 retry_count（retry all 语义
            # = 重试所有异常文件；2026-08-16 会话 9 修复：否则 retry_count≥上限 → M2 直接跳过）
            from pipeline_core.sidecar import read_sidecar, update_sidecar_field
            sidecar_dir = root / "sidecar"
            if sidecar_dir.exists():
                for scf in sidecar_dir.glob("*.json"):
                    try:
                        sc = read_sidecar(sidecar_dir, scf.stem)
                        if getattr(sc, "retry_count", 0):
                            update_sidecar_field(sidecar_dir, sc.doc_id, retry_count=0)
                    except Exception:
                        continue
        elif req.doc_id:
            # 按 doc_id 从 sidecar 查找对应文件名
            sidecar_dir = root / "sidecar"
            sc_path = sidecar_dir / f"{req.doc_id}.json"
            if sc_path.exists():
                sc_data = json.loads(sc_path.read_text(encoding="utf-8"))
                source = sc_data.get("source_file", "")
                for f in list(err_dir.iterdir()):
                    if f.is_file() and f.suffix != ".txt" and not f.name.endswith(".error.txt"):
                        if source in f.name or f.stem.startswith(req.doc_id):
                            dest = pending_dir / f.name
                            os.replace(str(f), str(dest))
                            moved += 1
                            _reset_sidecar_retry(root, req.doc_id)
                            break

        return RetryResponse(moved=moved)

    # ---- E-1：待处理队列操作（损坏文件强制处理 / 删除）----
    @app.post("/pending/{name}/force")
    def pending_force(name: str):
        """强制处理待处理文件：移除 [损坏] 前缀让 M1 重新尝试。

        损坏文件处理结果可能不完整——前端须显著警告后调用。
        """
        import os
        import urllib.parse
        name = urllib.parse.unquote(name)
        pending_dir = root / "00_待处理"
        for f in list(pending_dir.iterdir()) if pending_dir.exists() else []:
            if f.is_file() and f.name == name:
                if f.name.startswith("[损坏]"):
                    new_name = f.name[len("[损坏]"):]
                    new_path = pending_dir / new_name
                    os.replace(str(f), str(new_path))
                    return {"ok": True, "queued_name": new_name, "was_corrupt": True}
                return {"ok": True, "queued_name": f.name, "was_corrupt": False}
        raise HTTPException(404, "文件不在待处理队列")

    @app.delete("/pending/{name}")
    def pending_delete(name: str):
        """删除待处理条目（用户删除后重新提交完整文件）。"""
        import urllib.parse
        name = urllib.parse.unquote(name)
        pending_dir = root / "00_待处理"
        for f in list(pending_dir.iterdir()) if pending_dir.exists() else []:
            if f.is_file() and f.name == name:
                try:
                    f.unlink()
                except OSError as e:
                    raise HTTPException(500, f"删除失败: {e}")
                return {"ok": True, "deleted": name}
        raise HTTPException(404, "文件不在待处理队列")

    # ---- P3.5-1 第 1 步：论文引用清单（CSL JSON，正向生成）----
    # 项目目录：{pipeline_root}/papers/{paper_id}/（drafts/ output/ metadata.json bibliography.csl.json）
    # 兼容旧布局 papers/{paper_id}.csl.json → 读取时懒迁移

    def _safe_paper_id(paper_id: str) -> str:
        """校验 paper_id 防路径穿越（U3：委托 pipeline_core.pathsafe，转 4xx）。"""
        try:
            return safe_paper_id(paper_id)
        except PathUnsafeError as e:
            raise HTTPException(400, str(e))

    def _paper_dir(paper_id: str) -> Path:
        papers_dir = root / "papers"
        papers_dir.mkdir(exist_ok=True)
        d = papers_dir / paper_id
        d.mkdir(exist_ok=True)
        (d / "drafts").mkdir(exist_ok=True)
        (d / "output").mkdir(exist_ok=True)
        return d

    def _migrate_legacy_paper(paper_id: str) -> None:
        """旧布局 papers/{id}.csl.json → 新目录 bibliography.csl.json（懒迁移）。"""
        legacy = root / "papers" / f"{paper_id}.csl.json"
        if legacy.exists():
            d = _paper_dir(paper_id)
            target = d / "bibliography.csl.json"
            if not target.exists():
                legacy.replace(target)
            else:
                legacy.unlink()  # 已迁移过，清旧文件

    # ---- T1：inputs 参考资料入语义检索库（轻量直入 M4，同一向量空间）----

    # 可 ingest 扩展（第九/二十条口径：文本或可文本化；图片/表格只预览不接 ingest）
    INPUT_INDEXABLE_EXTS = {".pdf", ".docx", ".md", ".txt"}
    INPUT_SKIP_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".csv", ".xlsx"}

    def _paper_input_doc_ids(paper_id: str) -> set[str]:
        """当前项目 inputs_log 中已入库的 doc_id 集合（/search 徽标对照）。

        返回空集表示无关联（调用方照常返回结果，不加徽标）。
        """
        try:
            st = ws.load(_paper_dir(paper_id))
            return {r["doc_id"] for r in (st.get("inputs_log") or []) if r.get("doc_id")}
        except Exception:
            return set()

    def _remove_input_from_kb(root: Path, doc_id: str) -> None:
        """从检索库移除 inputs 向量（T1 删除联动）：向量库 + manifest delete + sidecar + 03/04 md。

        删除语义：doc_id = 内容哈希（多项目共享时同内容同 doc_id，删除即移除共享向量——
        引用计数不在本轮范围，工单按单项目视角）。队列残留由 md 移除兜底（未入库不残留）。
        """
        # 1) 删 sidecar 前读 source_file stem（03/04 的 md 以原名 stem 命名，反查用）
        stem = None
        sc_path = root / "sidecar" / f"{doc_id}.json"
        if sc_path.exists():
            try:
                from pipeline_core.sidecar import read_sidecar
                stem = Path(read_sidecar(root / "sidecar", doc_id).source_file).stem
            except Exception:
                stem = None
        # 2) 删 03_知识库原文/ 与 04_已入库归档/ 中该 doc 的 md（按 stem 前缀匹配）
        if stem:
            for qdir in (root / "03_知识库原文", root / "04_已入库归档"):
                if not qdir.exists():
                    continue
                for f in qdir.rglob("*.md"):
                    if f.stem == stem or f.stem.startswith(stem + "_"):
                        try:
                            f.unlink()
                        except OSError:
                            pass
        # 3) 删 sidecar（doc_id 关联的全部派生文件）+ 速览卡一等数据（digests/ 权威位）
        for p in root.glob(f"sidecar/{doc_id}*"):
            try:
                p.unlink()
            except OSError:
                pass
        _digest_remove(root, doc_id)
        # 4) 向量库 + manifest（op=delete；find_by_doc_hash 取 version 最大 → 幂等检查不再命中）
        try:
            from m4_vectordb.vector_store import delete_doc
            delete_doc(root / "vector_db" / "kb.db", doc_id)
        except Exception as e:
            logger.warning(f"[T1] 向量移除失败: {doc_id}: {e}")
        try:
            from pipeline_core.manifest import ManifestEntry, append_manifest
            append_manifest(
                root / "manifest.jsonl",
                ManifestEntry(doc_hash=doc_id, chunk_ids=[], version=0, ts="", op="delete"),
            )
        except Exception as e:
            logger.warning(f"[T1] manifest delete 记录失败: {doc_id}: {e}")

    def _read_meta(paper_id: str) -> dict:
        """项目元数据 metadata.json（name/created_at），缺省 name=paper_id。"""
        p = _paper_dir(paper_id) / "metadata.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    return d
            except Exception:
                pass
        return {"name": paper_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def _write_meta(paper_id: str, meta: dict) -> None:
        p = _paper_dir(paper_id) / "metadata.json"
        p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _list_papers() -> list[dict]:
        """列出所有论文项目：{paper_id, name, items_count, updated_at}。"""
        papers_dir = root / "papers"
        if not papers_dir.exists():
            return []
        result = []
        # 目录项目
        for d in sorted(papers_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            meta = _read_meta(d.name)
            biblio = d / "bibliography.csl.json"
            items = 0
            updated = ""
            if biblio.exists():
                try:
                    data = json.loads(biblio.read_text(encoding="utf-8"))
                    items = len(data.get("items", []))
                    updated = data.get("updated_at", "")
                except Exception:
                    pass
            result.append({
                "paper_id": d.name,
                "name": meta.get("name") or d.name,
                "items_count": items,
                "updated_at": updated or meta.get("created_at", ""),
            })
        # 旧 flat 文件项目（未迁移，触发迁移后并入）
        for f in sorted(papers_dir.glob("*.csl.json")):
            pid = f.name[: -len(".csl.json")]  # .stem 只去一个后缀 → demo-paper.csl，手动剥
            if any(r["paper_id"] == pid for r in result):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                items = len(data.get("items", []))
            except Exception:
                items = 0
            _migrate_legacy_paper(pid)
            result.append({
                "paper_id": pid,
                "name": pid,
                "items_count": items,
                "updated_at": "",
            })
        return result

    def _read_paper_biblio(paper_id: str) -> dict:
        # D8：读取逻辑归 bibliography_store（U1 权威路径）；此处仅接 legacy 迁移 + 项目目录
        _migrate_legacy_paper(paper_id)
        return bibliography_store.load(_paper_dir(paper_id))

    def _write_paper_biblio(paper_id: str, data: dict) -> None:
        # D8：保存逻辑归 bibliography_store；保留 legacy 迁移（与原写路径行为一致）
        _migrate_legacy_paper(paper_id)
        bibliography_store.save(_paper_dir(paper_id), data)

    @app.get("/papers")
    def papers_list():
        """论文项目列表（文献工作台项目下拉用）：{papers: [{paper_id, name, items_count}]}。"""
        return {"papers": _list_papers()}

    @app.get("/papers/{paper_id}")
    def paper_detail(paper_id: str):
        """项目详情：元数据 + 清单 + 草稿列表。"""
        paper_id = _safe_paper_id(paper_id)
        d = _paper_dir(paper_id)
        meta = _read_meta(paper_id)
        biblio = _read_paper_biblio(paper_id)
        drafts = []
        st = ws.ensure(d)
        for f in sorted((d / "drafts").glob("*.md")):
            if not f.is_file():
                continue
            st_ = f.stat()
            # 物理名 → 逻辑名（未映射的按原名，兼容旧数据）；只返回文件名（前端拼 /drafts/{name}）
            logical_name = ws.logical_name(d, st, f"drafts/{f.name}").split("/")[-1]
            drafts.append({
                "name": logical_name,
                "size": st_.st_size,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st_.st_mtime)),
            })
        has_output = any((d / "output").iterdir()) if (d / "output").exists() else False
        # 输入资料列表（权威目录 = 固化 paths.inputs；保留原名，不 rename，证据链）
        # 提交时间取固化 inputs_log（B 方案：不依赖文件 mtime），无记录回退 mtime
        inputs = []
        ws.ensure_inputs_log(d, st)
        log_map = {r["name"]: r.get("ts", "") for r in (st.get("inputs_log") or [])}
        inputs_dir = Path(st["paths"].get("inputs", "")) if st.get("paths") else d / "inputs"
        if inputs_dir.exists():
            for f in sorted(inputs_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    st_in = f.stat()
                    inputs.append({
                        "name": f.name,
                        "size": st_in.st_size,
                        "updated_at": log_map.get(f.name) or time.strftime(
                            "%Y-%m-%dT%H:%M:%S", time.localtime(st_in.st_mtime)),
                    })
        return {
            "paper_id": paper_id,
            "name": meta.get("name") or paper_id,
            "format": biblio.get("format", "GB/T 7714-2015"),
            "items": biblio.get("items", []),
            "drafts": drafts,
            "inputs": inputs,
            "has_output": has_output,
        }

    @app.post("/papers/{paper_id}/rename")
    def paper_rename(paper_id: str, req: dict | None = None):
        """项目改名（写 metadata.json，供宿主项目联动）。"""
        paper_id = _safe_paper_id(paper_id)
        name = ((req or {}).get("name") or "").strip()
        if not name:
            raise HTTPException(400, "缺少 name")
        meta = _read_meta(paper_id)
        meta["name"] = name[:80]
        meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _write_meta(paper_id, meta)
        return {"ok": True, "paper_id": paper_id, "name": meta["name"]}

    # ---- 创建论文项目（新建论文项目：自动带空模板草稿 + 工作台功能介绍 + 排版说明）----
    # 新建论文项目时投放的空模板草稿（开场引导，每步引导一个主要功能；开始写作/上传后即被用户内容替换）
    _DRAFT_TEMPLATE = """# 我的论文

> 你的论文工作台已建好。按下面几步开始：

## 接下来怎么做

1. **上传已有草稿**：点「↑ 上传草稿」上传 docx / md（或直接在下面写正文）。
2. **检索文献**：「📚 引用链」旁的「检索」标签——本地知识库搜索，或联网搜中英文文献。
3. **导入资料**：「输入资料」上传你的 PDF / 图片等文献原文，建好知识库。
4. **速览文献**：「速览卡」选一篇文档，AI 帮你提炼摘要、要点，找重点快人一步。
5. **引用文献**：在资料里拖选一句话 → 复制 → 到草稿粘贴 → 确认后段尾自动加 [N]。
6. **写作与保存**：在下面写正文，`Ctrl+S` 保存（自动快照 + 生成阶段 DOCX + 版本记录）。
7. **与 AI 协作**：右侧对话区讨论 / 点评草稿；「📚 引用链」管理引用清单。
8. **交付**：写完点「导出 DOCX」生成正式文档，参考文献按格式自动排列。

> 💡 以上是开场引导，每个功能点到为止；开始上传草稿或写正文后，会被你的内容替换。
"""

    @app.post("/papers")
    def paper_create(req: dict | None = None):
        """创建论文项目：{title} → 建项目目录 + 空模板草稿 + 空引用清单 + 版本 v1。

        返回 {ok, paper_id, draft_name}；用户可在新建时直接指定论文项目（显式入口）。
        """
        title = ((req or {}).get("title") or "").strip()
        if not title:
            raise HTTPException(400, "缺少 title")
        # paper_id：时间戳（避免中文/重名），安全字符
        paper_id = "p_" + time.strftime("%Y%m%d%H%M%S")
        # 防重：若冲突加序号
        while (root / "papers" / paper_id).exists():
            paper_id = "p_" + time.strftime("%Y%m%d%H%M%S") + "_" + str(int(time.time() * 1000) % 1000)
        d = _paper_dir(paper_id)
        st = ws.ensure(d)
        draft_name = "草稿.md"
        # 写空模板草稿（含工作台功能介绍/排版说明）
        p = _draft_path(paper_id, draft_name, write=True)
        if not p.exists():
            p.write_text(_DRAFT_TEMPLATE, encoding="utf-8")
            _snapshot_draft(paper_id, draft_name, _DRAFT_TEMPLATE, "create", "新建论文项目（空模板）")
        # 空引用清单
        bibliography_store.save(d, {"paper_id": paper_id, "format": "Chicago", "items": [], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        # metadata（论文名）
        _write_meta(paper_id, {"name": title[:80]})
        return {"ok": True, "paper_id": paper_id, "draft_name": draft_name, "title": title}

    def _draft_path(paper_id: str, draft_name: str, write: bool = False) -> Path:
        """草稿逻辑名 → 物理路径（经 workbench-state 映射）。

        write=True 写场景（新建/导入，自动注册映射）；默认读场景（映射必须已存在）。
        """
        # U3：草稿名校验委托 pipeline_core.pathsafe（中文兼容 + 禁 ..），转 4xx
        try:
            safe_draft_name(draft_name)
        except PathUnsafeError as e:
            raise HTTPException(400, str(e))
        d = _paper_dir(paper_id)
        st = ws.ensure(d)
        rel = f"drafts/{draft_name}"
        if write:
            return ws.register(d, st, rel)
        try:
            return ws.phys_path(d, st, rel)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    # ---- 草稿版本管理（V1）：每次保存快照 + versions.jsonl 动作日志 ----
    def _draft_history_dir(paper_id: str, draft_name: str) -> Path:
        return _paper_dir(paper_id) / "drafts" / "_history" / draft_name

    def _versions_log_path(paper_id: str) -> Path:
        return _paper_dir(paper_id) / "versions.jsonl"

    def _draft_version(paper_id: str, draft_name: str) -> int:
        """当前版本号 = 已有快照数 + 1（新草稿第 1 版）。"""
        hdir = _draft_history_dir(paper_id, draft_name)
        if hdir.exists():
            return len(list(hdir.glob("*.md"))) + 1
        return 1

    # versions.jsonl 读写锁：保存追加 与 AI 摘要后台回写 互斥
    _versions_log_lock = threading.Lock()

    def _append_version_log(paper_id: str, entry: dict) -> None:
        p = _versions_log_path(paper_id)
        with _versions_log_lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _diff_note_heuristic(prev: str, new: str) -> str:
        """启发式变更摘要（AI 不可用时的降级）：统计新增/删除行数。"""
        import difflib
        diff = difflib.unified_diff(prev.splitlines(), new.splitlines(), lineterm="")
        adds = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        dels = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        if not adds and not dels:
            return "内容无实质变化"
        parts = []
        if adds:
            parts.append(f"新增 {adds} 行")
        if dels:
            parts.append(f"删除 {dels} 行")
        return "、".join(parts)

    def _upgrade_version_note(paper_id: str, draft_name: str, version: int, prev: str, new: str) -> tuple[str, str] | None:
        """后台线程：AI 提炼本次保存「增量段落」的一句话摘要，回写 versions.jsonl note。

        增量段落 = new 中内容不在 prev 的段落（段落级对比，「改写几字」段落字符串不等即增量；
        首次保存 prev 空 → 全部段落）。无增量/AI 不可用/失败 → 返回 None（保留保存主流程
        已写的启发式备注）。返回 (note, anchor)：
          - note（≤50 字）：AI 摘要，供 T4 扫描作检索源；
          - anchor（≤200 字）：增量段落原文片段（同段落内能命中 insert_mark 的「原文子串」校验），
            优先取首个增量段；无可用的增量段落时回退 note。
        """
        prev_paras = {p.strip() for p in prev.split("\n\n") if p.strip()}
        added = [p.strip() for p in new.split("\n\n") if p.strip() and p.strip() not in prev_paras]
        if not added:
            return None
        added_text = "\n".join(added)
        try:
            from llm.providers import active_provider
            provider = active_provider()
        except Exception as e:
            logger.debug(f"变更摘要：provider 不可用，保留启发式备注: {e}")
            return None
        chat = getattr(provider, "_chat", None)
        if chat is None:  # stub 等无 chat 能力 → 保留启发式
            return None
        # 短超时保护：LLM 调用放内层线程，最多等 15s
        result: dict = {}

        def _call() -> None:
            try:
                result["note"] = chat(
                    "你是论文草稿的版本变更记录助手。根据给出的本次新增内容概括一句话。",
                    "以下是本次保存新增的段落（相对上一版）：\n"
                    f"{added_text[:4000]}\n\n"
                    "要求：一句话、不超过 50 字、只描述新增内容、不作评价，直接输出这句话。",
                )
            except Exception as e:
                logger.debug(f"变更摘要 LLM 调用失败（保留启发式备注）: {e}")

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(15)
        note = (result.get("note") or "").strip().splitlines()[0].strip()[:50] if result.get("note") else ""
        if not note:
            return None
        # anchor 候选：首个增量段落原文片段（须能作为 insert_mark 的段落子串；≤200 字符）
        anchor_src = None
        for p in added:
            if len(p.strip()) >= 6:
                anchor_src = p.strip()
                break
        if len(anchor_src or "") > 200:
            anchor_src = (anchor_src or "")[:200]
        anchor = (anchor_src or note)[:200]
        # 回写 versions.jsonl 中 (draft, version) 对应条目的 note
        log_path = _versions_log_path(paper_id)
        with _versions_log_lock:
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                return (note, anchor)
            out = []
            for line in lines:
                try:
                    e = json.loads(line)
                except Exception:
                    out.append(line)
                    continue
                if e.get("draft") == draft_name and e.get("version") == version:
                    e["note"] = note
                    line = json.dumps(e, ensure_ascii=False)
                out.append(line)
            log_path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        return (note, anchor)

    def _post_save_async(paper_id: str, draft_name: str, version: int, prev: str, content: str) -> None:
        """保存后台线程（串行）：先提炼增量摘要写备注，再按备注检索疑似引用建议。

        串行保证 T4 扫描拿到的是本轮生成的备注（无竞态）；任一步降级不阻塞另一环。
        """
        upgraded = _upgrade_version_note(paper_id, draft_name, version, prev, content)
        if upgraded is None:
            note, anchor = None, None
        else:
            note, anchor = upgraded
        _scan_missing_cites(paper_id, draft_name, note, anchor)

    # ---- T4：疑似引用建议面板（第十条定案，通道二）----
    # 共用保存钩子：保存主流程（快照+docx）同步完成，后台线程先提炼增量摘要（备注）
    # 再按备注检索库内（含 inputs），结果写 papers/{id}/citesuggestions.json。
    # 判据（2026-08-12 用户定案初版：单分数 ≥0.15；2026-08-28 用户定案改双条件）：
    #   备注检索 top1 命中才建议该文档。向量分数实测：真命中（改写段 vs 库内原文）0.12~0.28，
    #   无关内容 ≤0.03——单分数 0.15 会把 0.12~0.15 间的真命中漏报；
    #   故改为「top1 绝对分 ≥0.12 且 top1 与 top2 分差 >0.04」双条件（看排序拉开差距，更贴合
    #   「语义检索看排序不看分数」，BGE-M3+L2 实测见 T14 §6）。

    _CITE_SCAN_MIN_LEN = 8     # 备注最短检索长度（只挡「新增 3 行」等非语义启发式备注；真实 AI 摘要一句话常 10-50 字）
    _CITE_SCAN_ABS = 0.12      # 双条件①：top1 绝对分下限（真命中实测最低 0.1256/0.144；无关 ≤0.03）
    _CITE_SCAN_GAP = 0.04      # 双条件②：top1 与 top2 分差下限（排序拉开差距 → 真候选项 vs 泛匹配）

    def _cite_suggest_path(paper_id: str) -> Path:
        return _paper_dir(paper_id) / "citesuggestions.json"

    def _scan_missing_cites(paper_id: str, draft_name: str, note: str | None, anchor: str | None = None) -> None:
        """后台线程：按保存备注（增量摘要）检索库内 → 疑似漏引建议写文件。

        备注为空/过短（无增量或 AI 降级为启发式）→ 写空建议，不打扰（第十三条）。
        降级：嵌入不可用/异常 → 写空建议（不阻塞保存，下次保存重扫）。
        anchor：建议条目的插入标记锚文本——取草稿增量段落原文（能命中 insert_mark 的
        「段落子串」校验）；仅为检索而备，若未提供则回退 note。
        """
        try:
            if not note or len(note.strip()) < _CITE_SCAN_MIN_LEN:
                _cite_suggest_path(paper_id).write_text(
                    json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8"
                )
                return
            from m4_vectordb.search import search as m4_search
            from m4_vectordb.embedder import Embedder
            embedder = Embedder()
            if not embedder.check_alive():
                raise RuntimeError("嵌入服务不可用")
            results = m4_search(root, embedder, note, top_k=3, doc_hash=None)
            hits: list[dict] = []
            # 双条件判据（2026-08-28 用户定案，替代单分数 0.15）：
            #   ① top1 绝对分 ≥ _CITE_SCAN_ABS（0.12）——真命中下限，无关 ≤0.03 远低于此；
            #   ② top1 与「下一个不同 doc_hash 的最高分」分差 > _CITE_SCAN_GAP（0.04）——
            #      排序拉开差距（真候选项 vs 泛匹配）。须跨 doc 比较，因同 doc 相邻 chunk
            #      分数天然接近（自相关），不跨 doc 会把同 doc 相邻 chunk 的微小分差误判为「未拉开差距」。
            # 二者都满足才建议（「看排序不看分数」原则，BGE-M3+L2 实测见 T14 §6）。
            if results:
                top1 = results[0].get("score", 0.0)
                top1_doc = results[0].get("doc_hash")
                top2_doc_score = 0.0
                for r in results[1:]:
                    if r.get("doc_hash") != top1_doc:
                        top2_doc_score = r.get("score", 0.0)
                        break
                if top1 >= _CITE_SCAN_ABS and (top1 - top2_doc_score) > _CITE_SCAN_GAP:
                    hits = [results[0]]
            # 标题取 sidecar bibliography.title（回退 source_file/doc_id）
            def _doc_title(doc_id: str) -> str:
                try:
                    from pipeline_core.sidecar import read_sidecar
                    sc = read_sidecar(root / "sidecar", doc_id)
                    if sc.bibliography and sc.bibliography.get("title"):
                        return sc.bibliography["title"]
                    return Path(sc.source_file).name
                except Exception:
                    return doc_id
            items = [
                {
                    "idx": i,
                    "doc_id": h["doc_hash"],
                    "score": round(h.get("score", 0.0), 3),
                    "title": _doc_title(h["doc_hash"]),
                    # text/anchor：优先用增量段落原文（能命中 insert_mark「段落子串」），
                    # 无增量原文时回退 AI 摘要 note——避免 AI 摘要不在草稿中导致的插标失败。
                    "text": (anchor or note or "")[:200],
                    "anchor": (anchor or note or "")[:200],
                    "draft": draft_name,
                }
                for i, h in enumerate(hits)
            ]
            path = _cite_suggest_path(paper_id)
            path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[T4] 疑似引用扫描跳过（写空建议，保存不受影响）: {e}")
            try:
                _cite_suggest_path(paper_id).write_text(
                    json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

    @app.get("/papers/{paper_id}/citesuggestions")
    def paper_cite_suggestions(paper_id: str):
        """T4：读取最近一次保存的疑似引用建议（每次保存扫描覆盖前一轮）。

        返回 {items: [{idx, doc_id, score, title, text, anchor, draft}]}；无扫描记录返回空列表。
        """
        paper_id = _safe_paper_id(paper_id)
        p = _cite_suggest_path(paper_id)
        if not p.exists():
            return {"items": []}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"items": []}

    @app.post("/papers/{paper_id}/citesuggestions/dismiss")
    def paper_cite_suggest_dismiss(paper_id: str, req: dict | None = None):
        """T4：忽略一条建议（用户点「忽略」）→ 从当前建议文件移除（确认路径用 add+insert_mark）。"""
        paper_id = _safe_paper_id(paper_id)
        idx = (req or {}).get("idx")
        if not isinstance(idx, int):
            raise HTTPException(400, "缺少 idx")
        p = _cite_suggest_path(paper_id)
        if not p.exists():
            return {"ok": True, "items": []}
        try:
            items = json.loads(p.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            items = []
        remaining = [i for i in items if i.get("idx") != idx]
        # 重排 idx 连续
        for i, it in enumerate(remaining):
            it["idx"] = i
        p.write_text(json.dumps({"items": remaining}, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "items": remaining}

    def _snapshot_draft(paper_id: str, draft_name: str, content: str, action: str, note: str = "") -> int:
        """存快照 + 追加日志。返回新版本号。"""
        version = _draft_version(paper_id, draft_name)
        ts = time.strftime("%Y%m%dT%H%M%S")
        hdir = _draft_history_dir(paper_id, draft_name)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / f"v{version:03d}_{ts}.md").write_text(content, encoding="utf-8")
        _append_version_log(paper_id, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "draft": draft_name,
            "version": version,
            "note": (note or "")[:200],
            "user": "me",
        })
        return version

    @app.post("/papers/{paper_id}/drafts")
    def paper_draft_create(paper_id: str, req: dict | None = None):
        """新建草稿：{name} → 建空 md（默认带标题占位）+ create 版本记录。"""
        paper_id = _safe_paper_id(paper_id)
        name = ((req or {}).get("name") or "").strip()
        if not name:
            raise HTTPException(400, "缺少 name")
        if not name.endswith(".md"):
            name += ".md"
        p = _draft_path(paper_id, name, write=True)
        if not p.exists():
            content = f"# {name[:-3]}\n\n"
            p.write_text(content, encoding="utf-8")
            _snapshot_draft(paper_id, name, content, "create", "新建草稿")
        return {"ok": True, "name": name, "version": _draft_version(paper_id, name) - 1}

    @app.post("/papers/{paper_id}/drafts/import_docx")
    def paper_draft_import_docx(paper_id: str, req: dict | None = None):
        """docx/md 草稿导入（宿主上传后调用；目录由 _paper_dir 幂等创建，职责归管线）。

        {src_path} → pandoc 转 md 写 drafts/ + import_docx 版本记录（原文件由宿主管 inputs/，证据链）。
        可选 {draft_name}：指定写入的草稿逻辑名（用于"替换空模板"——同一会话仅一份草稿，
        引用链跟随该草稿，避免多草稿分叉）。缺省则用 src stem 作为新草稿名（保留原逻辑）。
        """
        paper_id = _safe_paper_id(paper_id)
        src_path = ((req or {}).get("src_path") or "").strip()
        if not src_path:
            raise HTTPException(400, "缺少 src_path")
        src = Path(src_path)
        if not src.is_file():
            raise HTTPException(404, f"源文件不存在: {src_path}")
        ext = src.suffix.lower()
        if ext not in (".docx", ".md"):
            raise HTTPException(400, f"仅支持 docx/md 草稿导入，得到 .{ext}")
        stem = src.stem or "draft"
        # 单草稿优先：给定 draft_name 则替换该草稿（复用其物理映射 = 覆盖写回，保留同一份草稿/引用链）；
        # 否则按上传文件名新建草稿（保留空模板）。
        draft_name = ((req or {}).get("draft_name") or "").strip() or f"{stem}.md"
        if not draft_name.endswith(".md"):
            draft_name += ".md"
        p = _draft_path(paper_id, draft_name, write=True)  # 写场景：注册映射（同名复用物理名 = 覆盖替换）
        if ext == ".docx":
            tmp_md = p.with_name(f".tmp_{p.name}")
            try:
                # S7：经 pipeline_core.pandoc 统一执行（exe 唯一取径 + proc.run + 错误归一），对外语义不变
                r = pandoc_convert(src, tmp_md)
                if not r.ok or not tmp_md.exists():
                    return {"ok": False, "reason": f"pandoc 转换失败: {r.stderr[:200]}"}
                tmp_md.replace(p)
            finally:
                tmp_md.unlink(missing_ok=True)
            note = "DOCX 草稿导入"
        else:
            p.write_bytes(src.read_bytes())
            note = "MD 草稿导入"
        content = p.read_text(encoding="utf-8")
        version = _snapshot_draft(paper_id, draft_name, content, "import_docx", note)
        return {"ok": True, "draft_name": draft_name, "version": version}

    @app.get("/papers/{paper_id}/drafts/{draft_name}")
    def paper_draft_get(paper_id: str, draft_name: str):
        """读草稿内容（宿主草稿编辑器用）。"""
        paper_id = _safe_paper_id(paper_id)
        p = _draft_path(paper_id, draft_name)
        if not p.exists():
            raise HTTPException(404, f"草稿不存在: {draft_name}")
        return {"name": draft_name, "content": p.read_text(encoding="utf-8"),
                "version": _draft_version(paper_id, draft_name) - 1,
                "latest_docx": _latest_docx_for_draft(paper_id, draft_name),
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))}

    def _latest_docx_for_draft(paper_id: str, draft_name: str) -> dict | None:
        """output/ 下该草稿最新的阶段 docx（经 workbench 映射查取，版本号最大）。M8a 打开交付件用。
        返回 {name: 逻辑文件名, path: 物理绝对路径}。"""
        d = _paper_dir(paper_id)
        st = ws.ensure(d)
        stem = draft_name[:-len(".md")] if draft_name.endswith(".md") else draft_name
        return ws.output_latest(d, st, stem)

    def _draft_to_docx(paper_id: str, draft_name: str, content: str, version: int) -> dict | None:
        """pandoc md → docx 阶段交付件（output/{stem}_v{n}.docx，物理名经映射）。pandoc 缺失/失败降级返回 None。"""
        d = _paper_dir(paper_id)
        st = ws.ensure(d)
        out_dir = d / "output"
        out_dir.mkdir(exist_ok=True)
        stem = draft_name[: -len(".md")]
        logical_rel = f"output/{stem}_v{version}.docx"
        out_file = ws.register(d, st, logical_rel)  # 确定性物理名 + 映射落盘
        tmp_md = out_dir / f"._{stem}_v{version}.md"
        try:
            tmp_md.write_text(content, encoding="utf-8")
            # S7：经 pipeline_core.pandoc 统一执行（exe 唯一取径 + proc.run + 错误归一），失败降级返回 None 语义不变
            r = pandoc_convert(tmp_md, out_file)
            if not r.ok or not out_file.exists():
                logger.warning(f"pandoc docx 生成失败: {r.stderr[:200]}")
                return None
            return {"path": str(out_file), "name": f"{stem}_v{version}.docx"}
        except Exception as e:
            logger.warning(f"pandoc 不可用（docx 阶段件跳过）: {e}")
            return None
        finally:
            tmp_md.unlink(missing_ok=True)

    @app.post("/papers/{paper_id}/drafts/{draft_name}")
    def paper_draft_save(paper_id: str, draft_name: str, req: dict | None = None):
        """保存草稿：{content, note?, action?} → 快照 + 日志 + 生成阶段 docx + 覆盖写工作副本。"""
        paper_id = _safe_paper_id(paper_id)
        p = _draft_path(paper_id, draft_name)
        if not p.exists():
            raise HTTPException(404, f"草稿不存在: {draft_name}")
        content = (req or {}).get("content")
        if not isinstance(content, str):
            raise HTTPException(400, "缺少 content")
        action = ((req or {}).get("action") or "edit")[:40]
        # 备注（用户定案）：不再前端弹框要人工备注；未提供时自动生成——
        # 先写启发式摘要（保存主流程零等待），AI 一句话摘要由后台线程生成后回写
        manual_note = ((req or {}).get("note") or "")[:200]
        prev_content = p.read_text(encoding="utf-8")
        has_history = _draft_version(paper_id, draft_name) > 1
        note = manual_note or (_diff_note_heuristic(prev_content, content) if has_history else "初始版本")
        version = _snapshot_draft(paper_id, draft_name, content, action, note)
        docx = _draft_to_docx(paper_id, draft_name, content, version)
        p.write_text(content, encoding="utf-8")
        if not manual_note and has_history:
            # 后台串行：先提炼增量摘要写备注（返回 note），再按备注检索疑似引用（T4）
            threading.Thread(
                target=_post_save_async,
                args=(paper_id, draft_name, version, prev_content, content),
                daemon=True,
            ).start()
        return {"ok": True, "name": draft_name, "chars": len(content), "version": version,
                "docx": docx,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    @app.get("/papers/{paper_id}/versions")
    def paper_versions(paper_id: str, draft: str = ""):
        """版本时间线：{draft} 过滤（缺省全部）→ 倒序 [{ts, action, draft, version, note}]。"""
        paper_id = _safe_paper_id(paper_id)
        log_path = _versions_log_path(paper_id)
        entries = []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("draft") and (not draft or e["draft"] == draft):
                        entries.append(e)
                except Exception:
                    continue
        entries.sort(key=lambda e: (e.get("draft", ""), e.get("version", 0)), reverse=True)
        return {"versions": entries}

    @app.post("/papers/{paper_id}/drafts/{draft_name}/rollback")
    def paper_draft_rollback(paper_id: str, draft_name: str, req: dict | None = None):
        """回滚到指定版本：{version} → 恢复前自动备份当前版（快照+日志），再写回目标快照。"""
        paper_id = _safe_paper_id(paper_id)
        p = _draft_path(paper_id, draft_name)
        if not p.exists():
            raise HTTPException(404, f"草稿不存在: {draft_name}")
        version = int((req or {}).get("version") or 0)
        if version < 1:
            raise HTTPException(400, "缺少 version")
        hdir = _draft_history_dir(paper_id, draft_name)
        snapshots = sorted(hdir.glob("v*.md")) if hdir.exists() else []
        target = next((s for s in snapshots if s.name.startswith(f"v{version:03d}_")), None)
        if target is None:
            raise HTTPException(404, f"版本 v{version} 不存在")
        # 恢复前自动备份当前工作副本
        current = p.read_text(encoding="utf-8")
        _snapshot_draft(paper_id, draft_name, current, "edit", "回滚前自动备份")
        # 写回目标版本
        target_content = target.read_text(encoding="utf-8")
        p.write_text(target_content, encoding="utf-8")
        new_version = _snapshot_draft(paper_id, draft_name, target_content, "rollback", f"回滚到 v{version}")
        return {"ok": True, "name": p.name, "version": new_version, "restored": version,
                "content": target_content}

    @app.post("/papers/{paper_id}/drafts/{draft_name}/insert_mark")
    def paper_draft_insert_mark(paper_id: str, draft_name: str, req: dict | None = None):
        """M7 深度对话：在草稿指定段落末尾插入引用标记 [N]。

        {anchor, doc_id} → 定位含 anchor 的段落 → 段尾追加 [N]（N 为 doc_id 在清单中的编号）
        → 快照 + insert_ref 版本记录 → 写回工作副本。
        正向生成（清单编号即 [N]），零解析。无匹配锚段落/文献未登记则报错不改文件。
        """
        paper_id = _safe_paper_id(paper_id)
        p = _draft_path(paper_id, draft_name)
        if not p.exists():
            raise HTTPException(404, f"草稿不存在: {draft_name}")
        anchor = ((req or {}).get("anchor") or "").strip()
        doc_id = ((req or {}).get("doc_id") or "").strip()
        if not anchor or not doc_id:
            raise HTTPException(400, "缺少 anchor 或 doc_id")
        if len(anchor) > 200:
            raise HTTPException(400, "anchor 过长（≤200 字符）")
        # 文献须已登记（编号 = 清单序号）
        data = _read_paper_biblio(paper_id)
        item = bibliography_store.find_by_doc_id(data, doc_id)
        if item is None:
            raise HTTPException(400, f"文献 {doc_id} 未在引用清单中（先用 kb_add_ref 登记）")
        seq = item.get("seq")
        content = p.read_text(encoding="utf-8")
        # 定位含 anchor 的段落（按空行分块，段落内可含换行）；取最后出现处
        blocks = content.split("\n\n")
        hit_idx = None
        for i, b in enumerate(blocks):
            if anchor in b:
                hit_idx = i
        if hit_idx is None:
            raise HTTPException(404, f"草稿中未找到锚文本「{anchor[:30]}…」的段落（请用更贴近段尾的原文片段）")
        mark = f"[{seq}]"
        if f"[{seq}]" in blocks[hit_idx]:
            return {"ok": False, "reason": f"该段落已含 [N] 标记（{mark}）", "seq": seq}
        # 段尾（去尾空白后）追加标记；末尾为图片/空则自然落在段尾
        blocks[hit_idx] = blocks[hit_idx].rstrip() + mark
        new_content = "\n\n".join(blocks)
        version = _snapshot_draft(paper_id, draft_name, new_content, "insert_ref", f"段落插入引用 [{seq}]（{doc_id}）")
        p.write_text(new_content, encoding="utf-8")
        return {"ok": True, "draft_name": p.name, "seq": seq, "version": version,
                "anchor": anchor[:100]}

    @app.get("/papers/{paper_id}/inputs/{input_name}")
    def paper_input_file(paper_id: str, input_name: str):
        """读取项目 inputs/ 下的用户输入文件（M8b 宿主只读预览用）。

        input_name 必须是 inputs/ 目录中真实存在的文件名（禁止路径穿越）；
        四类（文档/图片/表格 + pdf）均可下载，宿主按扩展名选择渲染方式；
        图片给 image/* content-type（内联 <img> 显示需要）。
        """
        paper_id = _safe_paper_id(paper_id)
        inputs_dir = _paper_dir(paper_id) / "inputs"
        if not inputs_dir.is_dir():
            raise HTTPException(404, "inputs 目录不存在")
        # U3：inputs 文件名单组件校验委托 pipeline_core.pathsafe，转 4xx
        try:
            name = safe_filename(input_name)
        except PathUnsafeError as e:
            raise HTTPException(400, str(e))
        f = inputs_dir / name
        if not f.is_file():
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        ext = f.suffix.lower()
        media = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
            ".csv": "text/csv; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(ext, "application/octet-stream")
        from urllib.parse import quote
        fname_ascii = f.name.encode("ascii", "ignore").decode() or ("file" + ext)
        disposition = f"inline; filename=\"{fname_ascii}\"; filename*=UTF-8''{quote(f.name)}"
        # FileResponse 自动管理文件句柄（StreamingResponse(open()) 惰性读会泄漏句柄，曾致文件删不掉）
        from fastapi.responses import FileResponse
        return FileResponse(str(f), media_type=media,
                            headers={"Content-Disposition": disposition})

    # xlsx 表格预览上限（资料 Tab 方案 A）
    PREVIEW_TABLE_MAX_ROWS = 500
    PREVIEW_TABLE_MAX_COLS = 50
    PREVIEW_TABLE_CELL_MAX = 200
    PREVIEW_TABLE_MAX_BYTES = 20 * 1024 * 1024

    @app.get("/papers/{paper_id}/inputs/{input_name}/preview_table")
    def paper_input_preview_table(paper_id: str, input_name: str):
        """xlsx 只读表格预览：第一个 sheet → {columns, rows, total_rows, truncated}。

        边界（第九条口径）：本轮表格只预览，不接入 ingest/检索（T1 工单范围不变）。
        U3：文件名单组件校验委托 pipeline_core.pathsafe（非法名按 404 处理，不暴露目录细节）；
        仅 .xlsx；>20MB 直接 413；openpyxl 缺失 → 503 明确提示。
        """
        paper_id = _safe_paper_id(paper_id)
        inputs_dir = _paper_dir(paper_id) / "inputs"
        if not inputs_dir.is_dir():
            raise HTTPException(404, "inputs 目录不存在")
        try:
            name = safe_filename(input_name)
        except PathUnsafeError:
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        f = inputs_dir / name
        if not f.is_file():
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        if f.suffix.lower() != ".xlsx":
            raise HTTPException(404, "表格预览仅支持 .xlsx 文件")
        if f.stat().st_size > PREVIEW_TABLE_MAX_BYTES:
            raise HTTPException(413, "文件超过 20MB 预览上限（请用系统程序打开）")
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise HTTPException(503, "管线缺少 openpyxl 依赖（pip install openpyxl 后重启管线）")
        try:
            wb = load_workbook(f, read_only=True, data_only=True)
        except Exception as e:
            raise HTTPException(400, f"xlsx 解析失败：{e}")
        rows: list[list[str]] = []
        total = 0
        try:
            ws = wb.worksheets[0] if wb.worksheets else None
            if ws is not None:
                for r in ws.iter_rows(values_only=True):
                    total += 1
                    if len(rows) >= PREVIEW_TABLE_MAX_ROWS:
                        continue
                    rows.append([
                        (str(c)[:PREVIEW_TABLE_CELL_MAX] if c is not None else "")
                        for c in r[:PREVIEW_TABLE_MAX_COLS]
                    ])
        finally:
            wb.close()
        return {
            "columns": rows[0] if rows else [],
            "rows": rows[1:] if rows else [],
            "total_rows": total,
            "truncated": total > PREVIEW_TABLE_MAX_ROWS,
        }

    @app.post("/papers/{paper_id}/inputs/{input_name}/index")
    def paper_input_index(paper_id: str, input_name: str):
        """T1：项目参考资料入语义检索库（轻量直入 M4，与文献同一向量空间）。

        可 ingest：md/txt 直读、docx 经 pandoc 转 md、pdf 经 fitz 提取文本层；
        不 ingest：图片/表格（第二十条定案：只预览不接 ingest）、扫描件 PDF（无文本层，
        提示走文献库通道）、其他类型（返回明确拒绝）。
        流程：doc_id = 内容哈希 → 判重（sidecar 已存在 = 已入库/入队）→ 提取文本写
        03_知识库原文/{原名stem}.md + 建轻量 sidecar → M4 watcher 异步入库
        （嵌入离线自愈拉起，md 留队列）；doc_id 回写 inputs_log（/search 徽标对照）。
        """
        paper_id = _safe_paper_id(paper_id)
        inputs_dir = _paper_dir(paper_id) / "inputs"
        if not inputs_dir.is_dir():
            raise HTTPException(404, "inputs 目录不存在")
        try:
            name = safe_filename(input_name)
        except PathUnsafeError:
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        f = inputs_dir / name
        if not f.is_file():
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        ext = f.suffix.lower()
        if ext in INPUT_SKIP_EXTS:
            return {"ok": False, "indexed": False, "reason": "图片/表格本轮只预览不接 ingest（第九/二十条口径）"}
        if ext not in INPUT_INDEXABLE_EXTS:
            return {"ok": False, "indexed": False, "reason": f"不支持入库的文件类型: {ext}"}
        # 内容哈希 doc_id（判重锚点，多项目共享同内容天然去重）
        doc_id = compute_doc_id(f)
        sidecar_dir = root / "sidecar"
        st = ws.ensure(_paper_dir(paper_id))
        if (sidecar_dir / f"{doc_id}.json").exists():
            # 已入库/已在队列：不重复入库，仅回写 inputs_log 关联（A/C 4 判重）
            ws.set_input_doc_id(_paper_dir(paper_id), st, name, doc_id)
            return {"ok": True, "indexed": False, "duplicate": True, "doc_id": doc_id}
        # 提取文本 → 03_知识库原文/{原名stem}.md（M4 watcher 消费；md 名 = 原名 stem 便于删除反查）
        stem = Path(name).stem
        md = root / "03_知识库原文" / f"{stem}.md"
        md.parent.mkdir(parents=True, exist_ok=True)  # 空库/新库可能无 03 目录（M0/M1 才建），幂等创建
        try:
            if ext in (".md", ".txt"):
                # 编码纪律 6.3：不得假设 UTF-8——BOM 按 BOM 解、严格 UTF-8 优先、失败回退 GBK（Excel 常见导出）
                raw = f.read_bytes()
                text = None
                if raw.startswith(b"\xef\xbb\xbf"):
                    text = raw[3:].decode("utf-8")
                elif raw.startswith(b"\xff\xfe"):
                    text = raw[2:].decode("utf-16le")
                elif raw.startswith(b"\xfe\xff"):
                    text = raw[2:].decode("utf-16be")
                else:
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text = raw.decode("gbk", errors="replace")
                md.write_text(text, encoding="utf-8")
            elif ext == ".docx":
                r = pandoc_convert(f, md)
                if not r.ok:
                    return {"ok": False, "indexed": False, "reason": f"pandoc 转换失败: {r.stderr[:200]}"}
            elif ext == ".pdf":
                import fitz
                doc = fitz.open(str(f))
                try:
                    # 逐页带 <!-- page N --> 标注拼接（对齐 M2 ocr.md 格式；
                    # 缺标注速览卡页码退化为全 1——2026-08-13 走查发现）
                    pages_text = [p.get_text("text") for p in doc]
                finally:
                    doc.close()
                if not "".join(pages_text).strip():
                    return {"ok": False, "indexed": False,
                            "reason": "该 PDF 为扫描件（无文本层），本轮不接 ingest；请走文献库入库通道"}
                md.write_text(
                    "\n\n".join(f"<!-- page {i + 1} -->\n{t}" for i, t in enumerate(pages_text)),
                    encoding="utf-8",
                )
        except Exception as e:
            raise HTTPException(500, f"文本提取失败：{e}")
        # 建轻量 sidecar（index_document 据此匹配 doc_id 与 source_file=原名）
        from pipeline_core.sidecar import init_sidecar
        init_sidecar(sidecar_dir, doc_id, name, page_count=0, translate_flag=False)
        # 回写 inputs_log 关联（/search 徽标对照）
        ws.set_input_doc_id(_paper_dir(paper_id), st, name, doc_id)
        return {"ok": True, "indexed": True, "queued": True, "doc_id": doc_id}

    @app.delete("/papers/{paper_id}/inputs/{input_name}")
    def paper_input_delete(paper_id: str, input_name: str):
        """删除项目输入文件（回收站语义，工程原则 P3 红线：不物理删除）。

        移动到 inputs/_trash/{YYYYMMDD-HHMMSS}_{name}（无 _trash 则建），并移除 inputs_log 登记。
        仅允许删 inputs/ 直接子文件：U3 pathsafe 单组件校验（穿越/子路径按 404），
        目录（含 _trash 本身）与 _trash 内文件不可经本端点触碰。

        T1 挂点（已接）：删除同步从检索库移除（向量 chunks + manifest delete + sidecar + 03/04 md）。
        """
        import shutil
        paper_id = _safe_paper_id(paper_id)
        inputs_dir = _paper_dir(paper_id) / "inputs"
        if not inputs_dir.is_dir():
            raise HTTPException(404, "inputs 目录不存在")
        # U3：inputs 文件名单组件校验委托 pipeline_core.pathsafe（非法名按 404，不暴露目录细节）
        try:
            name = safe_filename(input_name)
        except PathUnsafeError:
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        if name == "_trash":
            raise HTTPException(404, "不允许操作 _trash 归档")
        f = inputs_dir / name
        if not f.is_file():  # 目录（含 _trash）亦落此分支
            raise HTTPException(404, f"输入文件不存在: {input_name}")
        # T1：移动前算 doc_id（内容哈希），删除后从检索库移除
        try:
            doc_id = compute_doc_id(f)
        except Exception as e:
            doc_id = None
            logger.warning(f"[T1] 删除时 doc_id 计算失败（跳过向量移除）: {name}: {e}")
        trash = inputs_dir / "_trash"
        trash.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        target = trash / f"{ts}_{name}"
        n = 1
        while target.exists():  # 同秒同名归档冲突保险
            target = trash / f"{ts}_{n}_{name}"
            n += 1
        shutil.move(str(f), str(target))
        st = ws.ensure(_paper_dir(paper_id))
        ws.remove_inputs_log(_paper_dir(paper_id), st, name)
        if doc_id:
            _remove_input_from_kb(root, doc_id)
        return {"ok": True, "trashed_to": target.name}

    @app.post("/debug-log")
    def debug_log(req: dict | None = None):
        """前端诊断日志（宿主组件正常操作时上报，写 logs/frontend-debug.log，用户无感）。"""
        msg = (req or {}).get("msg") or ""
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            log_dir = root / "logs"
            log_dir.mkdir(exist_ok=True)
            with open(log_dir / "frontend-debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass
        return {"ok": True}



    def _gen_cite_draft_for(doc_id: str, fmt: str) -> dict | None:
        """复用 /cite 核心逻辑：confirmed sidecar 优先，否则现场提取。返回 {draft, formatted, confirmed} 或 None。"""
        from pipeline_core.sidecar import read_sidecar
        try:
            sc = read_sidecar(root / "sidecar", doc_id)
        except Exception:
            return None
        if sc.bibliography and sc.metadata_status == "confirmed":
            return {
                "draft": sc.bibliography,
                "formatted": f"[confirmed] {sc.bibliography.get('title', '')}",
                "confirmed": True,
            }
        try:
            from m6_cite.cite_skill import firstpage_to_biblio
            fp_path = root / "sidecar" / f"{doc_id}_firstpage.txt"
            fp_text = fp_path.read_text(encoding="utf-8") if fp_path.exists() else ""
            result = firstpage_to_biblio(fp_text, None, fmt)
            return {
                "draft": result.biblio,
                "formatted": result.formatted,
                "confirmed": False,
            }
        except ImportError:
            return None

    @app.get("/papers/{paper_id}/bibliography")
    def paper_bibliography(paper_id: str):
        paper_id = _safe_paper_id(paper_id)
        data = _read_paper_biblio(paper_id)
        return data

    @app.post("/papers/{paper_id}/bibliography/add")
    def paper_bibliography_add(paper_id: str, req: dict):
        """添加引用：{doc_id} → 提取/确认 → 去重追加。

        可选字段（选区引用场景）：page（页码锚点，A4 回跳用）、snippet（原文片段，核验/资料卡用）；
        draft_id/anchor（论文内锚点，M5 草稿联动：引用标记插入的草稿与段落）。
        """
        paper_id = _safe_paper_id(paper_id)
        doc_id = (req or {}).get("doc_id")
        if not doc_id:
            raise HTTPException(400, "缺少 doc_id")
        data = _read_paper_biblio(paper_id)
        fmt = data.get("format", bibliography_store.DEFAULT_FORMAT)
        existing = bibliography_store.find_by_doc_id(data, doc_id)
        if existing is not None:
            return {"ok": False, "reason": "该文献已在清单中", "seq": existing.get("seq")}
        draft = _gen_cite_draft_for(doc_id, fmt)
        if draft is None:
            raise HTTPException(404, "文献不存在或无法生成引用")
        item = bibliography_store.add_item(
            data, doc_id, draft,
            page=(req or {}).get("page"),
            snippet=(req or {}).get("snippet"),
            draft_id=(req or {}).get("draft_id"),
            anchor=(req or {}).get("anchor"),
        )
        _write_paper_biblio(paper_id, data)
        return {"ok": True, "item": item}

    @app.post("/papers/{paper_id}/bibliography/remove")
    def paper_bibliography_remove(paper_id: str, req: dict):
        """移除引用：{seq} → 移除并重排编号。"""
        paper_id = _safe_paper_id(paper_id)
        seq = (req or {}).get("seq")
        if seq is None:
            raise HTTPException(400, "缺少 seq")
        data = _read_paper_biblio(paper_id)
        if not bibliography_store.remove_item(data, seq):
            return {"ok": False, "reason": "未找到该条目"}
        _write_paper_biblio(paper_id, data)
        return {"ok": True, "items": data["items"]}

    @app.post("/papers/{paper_id}/bibliography/format")
    def paper_bibliography_format(paper_id: str, req: dict):
        """切换格式：{format} → 全量重渲染（编号=顺序，正向生成）。"""
        paper_id = _safe_paper_id(paper_id)
        fmt = (req or {}).get("format")
        if fmt not in bibliography_store.FORMATS:
            raise HTTPException(400, f"不支持的格式: {fmt}")
        data = _read_paper_biblio(paper_id)
        bibliography_store.set_format(data, fmt)
        _write_paper_biblio(paper_id, data)
        return {"ok": True, "format": fmt, "items": data["items"]}

    @app.post("/papers/{paper_id}/bibliography/verify")
    def paper_bibliography_verify(paper_id: str, req: dict):
        """核验清单条目：{seq, claim} → 对照原文判定 → 回写条目 verified 字段。"""
        paper_id = _safe_paper_id(paper_id)
        seq = (req or {}).get("seq")
        claim = ((req or {}).get("claim") or "").strip()
        if seq is None:
            raise HTTPException(400, "缺少 seq")
        if not claim:
            raise HTTPException(400, "缺少论点 claim（粘贴你在论文中写的论断）")
        data = _read_paper_biblio(paper_id)
        item = bibliography_store.find_by_seq(data, seq)
        if item is None:
            raise HTTPException(404, "未找到该清单条目")
        doc_id = item.get("doc_id")
        if not doc_id:
            raise HTTPException(404, "条目缺少 doc_id")
        md = _find_doc_md(doc_id)
        if md is None:
            raise HTTPException(404, "该文献未入库，无法核验（需先经管线处理）")
        from m6_cite.verify import verify_citation
        result = verify_citation(
            doc_id,
            claim,
            md.read_text(encoding="utf-8"),
            providers.active_provider(),
        )
        item["verified"] = result
        _write_paper_biblio(paper_id, data)
        return {"ok": True, "verified": result}

    @app.get("/docs/{doc_id}/verify")
    def doc_verify(doc_id: str, claim: str = ""):
        """单文档核验：?claim=...  → 对照原文判定。"""
        if not claim.strip():
            raise HTTPException(400, "缺少 claim 参数（?claim=你在论文中的论断）")
        md = _find_doc_md(doc_id)
        if md is None:
            raise HTTPException(404, "该文献未入库，无法核验")
        from m6_cite.verify import verify_citation
        return verify_citation(
            doc_id,
            claim,
            md.read_text(encoding="utf-8"),
            providers.active_provider(),
        )

    # ---- POST /search ----
    @app.post("/search", response_model=SearchResponse)
    def search(req: SearchRequest):
        try:
            from m4_vectordb.search import search as m4_search
            from m4_vectordb.embedder import Embedder
            embedder = Embedder()
            # Q4（2026-08-13 用户定案）：中文 query 联合英译词双路检索——避免库内英文
            # 文献因中文 query 漏命中。翻译复用 T5 链（云端优先，会话缓存）；完全本地
            # 模式/翻译不可用 → 单路原词（行为不变）。按 chunk 合并取高分。
            from m7_websearch.config import web_search_available
            from m7_websearch.translate import contains_cjk, translate_query

            queries = [req.query]
            query_en = None
            if contains_cjk(req.query) and web_search_available():
                tr = translate_query(req.query)
                if tr["method"] != "none" and tr["translated"] and tr["translated"] != req.query:
                    queries.append(tr["translated"])
                    query_en = tr["translated"]

            merged: dict[str, dict] = {}
            for q in queries:
                for r in m4_search(root, embedder, q, req.top_k, req.doc_hash):
                    cur = merged.get(r["chunk_id"])
                    if cur is None or r["score"] > cur["score"]:
                        merged[r["chunk_id"]] = r
            results = sorted(merged.values(), key=lambda x: -x["score"])[: req.top_k]
            # T1：对照当前项目 inputs_log 的 doc_id，命中标记「本项目资料」（视图标注，非隔离）
            if req.paper_id:
                try:
                    pid = safe_paper_id(req.paper_id)
                except PathUnsafeError:
                    pid = None
                if pid:
                    owned = _paper_input_doc_ids(pid)
                    if owned:
                        for r in results:
                            r["is_project_input"] = r["doc_hash"] in owned
            return SearchResponse(results=[SearchResultItem(**r) for r in results], query_en=query_en)
        except RuntimeError as e:
            # 嵌入模型不可用等运行时错误：返回 HTTP 503 + 明确信息（前端可展示）
            raise HTTPException(503, str(e))
        except (ImportError, OSError):
            # 模块缺失/库文件问题 → 返回空（前端显示"未找到"）
            return SearchResponse(results=[])

    # ---- POST /search/quote —— B1：限定单文档检索原文证据（引用核对用）----
    @app.post("/search/quote")
    def search_quote(req: dict):
        """quote_verify：给定 doc_id + 检索词 → 单文档向量检索 → 返回原文片段（页码锚点）。

        用于引用核对时"从原文找证据"。嵌入模型不可用 → 回退启发式关键词。
        """
        doc_id = (req or {}).get("doc_id")
        query = ((req or {}).get("query") or "").strip()
        top_k = (req or {}).get("top_k") or 5
        if not doc_id or not query:
            raise HTTPException(400, "缺少 doc_id 或 query")
        # 向量检索
        try:
            from m4_vectordb.search import search as m4_search
            from m4_vectordb.embedder import Embedder
            results = m4_search(root, Embedder(), query, top_k, doc_id)
            return {"mode": "vector", "results": results}
        except RuntimeError:
            pass
        except (ImportError, OSError):
            pass
        # 回退：原文关键词启发式
        md = _find_doc_md(doc_id)
        if md is None:
            raise HTTPException(404, "该文献未入库，无法定位原文")
        text = md.read_text(encoding="utf-8")
        from m6_cite.verify import _locate_relevant
        snippets = _locate_relevant(text, query, top_n=top_k)
        return {
            "mode": "heuristic",
            "results": [
                {
                    "chunk_id": f"{doc_id}_kw{i}",
                    "doc_hash": doc_id,
                    "text": s[:300],
                    "score": 0.0,
                    "doc_meta": {"source_file": md.name, "title": None, "page_estimate": None},
                }
                for i, s in enumerate(snippets)
            ],
        }

    # ---- POST /provider —— 分析后端切换（P3.5-1 第 4 步）----
    @app.post("/provider")
    def set_provider_mode(req: dict | None = None):
        """切换分析后端：local | cloud | off。返回 {success, mode, message, provider}。"""
        from llm import providers
        req = req or {}
        mode = req.get("mode")
        if mode is None:
            return {
                "success": True,
                "mode": providers.get_analysis_backend(),
                "message": f"当前后端：{providers.get_analysis_backend()}",
                "provider": getattr(providers.active_provider(), "name", ""),
            }
        return providers.set_analysis_backend(mode)

    # ---- POST /cite ----
    @app.post("/cite", response_model=CiteResponse)
    def cite(req: CiteRequest):
        # P3.5-1 第 4 步：保证 provider 已注入（修复此前 /cite 恒走 stub 的问题）
        from llm import providers
        providers.ensure_analysis()
        try:
            from pipeline_core.sidecar import read_sidecar
            sc = read_sidecar(root / "sidecar", req.doc_id)
            if sc.bibliography and sc.metadata_status == "confirmed":
                return CiteResponse(
                    draft=sc.bibliography,
                    field_confidence={k: 1.0 for k in sc.bibliography},
                    formatted=f"[confirmed] {sc.bibliography.get('title', '')}",
                )
            # 尝试调用 M6
            try:
                from m6_cite.cite_skill import firstpage_to_biblio
                fp_path = root / "sidecar" / f"{req.doc_id}_firstpage.txt"
                fp_text = fp_path.read_text(encoding="utf-8") if fp_path.exists() else ""
                result = firstpage_to_biblio(fp_text, None, req.format)
                return CiteResponse(
                    draft=result.biblio,
                    field_confidence=result.field_confidence,
                    formatted=result.formatted,
                )
            except ImportError:
                pass
            # fallback stub
            return CiteResponse(
                draft={},
                field_confidence={},
                formatted=f"[stub] {sc.source_file}",
            )
        except Exception:
            return CiteResponse(
                draft={},
                field_confidence={},
                formatted="[stub] 无法生成引用",
            )

    # ---- P3.5-1 B3：论文全文翻译旁路（本地，复用 M3 双语结构，不入队）----
    @app.post("/docs/{doc_id}/translate")
    def doc_translate(doc_id: str, req: dict | None = None):
        """单篇快速翻译：读原文 md → 分块 → provider.translate(en→zh) → 双语 MD。"""
        force = (req or {}).get("force", False)
        out = root / "03_知识库原文" / f"{doc_id}.bilingual.md"
        if out.exists() and not force:
            return {"ok": True, "cached": True, "path": str(out), "text": out.read_text(encoding="utf-8")}
        md = _find_doc_md(doc_id)
        if md is None:
            raise HTTPException(404, "该文献未入库，无法翻译（需先经管线处理）")
        provider = providers.active_provider()
        if provider is None or getattr(provider, "name", "") == "stub":
            raise HTTPException(503, "翻译模型未运行（llama.cpp 未启动），请先在环境向导/模型面板启动 Qwen2.5-3B 或 Hy-MT2")
        text = md.read_text(encoding="utf-8")
        try:
            from m3_translator.translator import build_bilingual_md, parse_md_blocks
            blocks = parse_md_blocks(text, {"lang_foreign_ratio": 0.80, "block_min_chars": 50})
        except ImportError:
            raise HTTPException(500, "翻译模块缺失")
        translate_blocks = [b for b in blocks if b.action == "translate"]
        if len(translate_blocks) > 50:
            return {"ok": False, "hint": f"文档翻译块 {len(translate_blocks)} 个较多，建议走导入管线全量翻译（异步）；本旁路适合较短文档。"}
        done = 0
        for b in translate_blocks:
            try:
                b.translated = provider.translate(b.text, "en", "zh")
            except Exception as e:
                logger.warning(f"翻译块 {b.seq} 失败: {e}")
                b.translated = f"[翻译失败] {b.text}"
            done += 1
        bilingual = build_bilingual_md(blocks)
        out.write_text(bilingual, encoding="utf-8")
        return {
            "ok": True, "cached": False, "path": str(out),
            "translated_blocks": done, "total_blocks": len(blocks),
            "text": bilingual,
        }

    # ---- P3.5-1 B4：联网文献检索（arXiv 开放 API，默认开 + 来源声明）----
    @app.post("/web/literature_search")
    def web_literature_search(req: dict):
        """按 topic 检索 arXiv 开放 API。结果带来源可核对 + 检索日期。

        默认开启；config analysis.web_search_enabled=false 可关回完全本地。
        agent 工具（kb_lit_search）与 webui 面板共用本路由，输出统一带来源声明。
        T6：解析实现已迁入 ArxivAdapter（前端独立入口同步迁入「国际文献」标签），
        本端点保留为 agent/webui 兼容门面，复用适配器避免双份解析代码。
        """
        topic = ((req or {}).get("topic") or "").strip()
        max_results = int((req or {}).get("max_results") or 8)
        if not topic:
            raise HTTPException(400, "缺少 topic")
        # U4：开关读权威 config；出站经 http_client 统一封装（适配器内部）
        from pipeline_core.http_client import web_search_enabled
        if not web_search_enabled():
            return {"ok": False, "enabled": False, "message": "联网检索已在配置中关闭（analysis.web_search_enabled=false），可在完全本地模式下重新开启。"}
        from m7_websearch.adapters.arxiv import ArxivAdapter

        items = [
            {
                "title": r.title[:200],
                "authors": r.authors[:10],
                "year": r.year or "",
                "url": r.url,
                "abstract": (r.abstract or "")[:300],
            }
            for r in ArxivAdapter().search(topic, max_results)
        ]
        import datetime

        search_date = datetime.date.today().isoformat()
        return {
            "ok": True, "enabled": True,
            "source": "arXiv 开放 API",
            "search_date": search_date,
            "disclaimer": f"结果来自 arXiv 开放 API，检索于 {search_date}，仅供文献发现，引用前请回原文核对。",
            "topic": topic,
            "results": items,
        }

    # ---- T5：联网检索适配层（统一 schema + 关键词英译降级链；蓝图第十六/十七/十八条）----
    _WEB_SEARCH_DISABLED_MSG = "联网检索不可用：当前为完全本地模式（analysis.web_search_enabled=false 或分析后端=完全本地）。"

    @app.get("/web/search_status")
    def web_search_status():
        """联网检索可用态（A/C 5：完全本地模式整体不可用，前端据此显示不可用态）。"""
        from m7_websearch.config import web_search_available

        enabled = web_search_available()
        return {"ok": True, "enabled": enabled, "message": "" if enabled else _WEB_SEARCH_DISABLED_MSG}

    @app.post("/web/translate_query")
    def web_translate_query(req: dict):
        """关键词英译（A/C 2/A/C 3：结果界面可见可改；降级链 云端→本地→不翻译+提示）。"""
        query = ((req or {}).get("query") or "").strip()
        if not query:
            raise HTTPException(400, "缺少 query")
        from m7_websearch.config import web_search_available

        if not web_search_available():
            return {"ok": False, "enabled": False, "message": _WEB_SEARCH_DISABLED_MSG}
        from m7_websearch.translate import translate_query

        r = translate_query(query)
        return {"ok": True, "enabled": True, **r}

    @app.post("/web/search")
    def web_search(req: dict):
        """统一联网检索（适配器扇出 + 统一 schema + 来源标签）。

        query_en 由前端传入（用户看过/改过的翻译词）时跳过翻译直接检索；
        否则走 translate_query 降级链并把翻译结果回传（界面显示「已翻译为」）。
        channel="zh"（T7 中文文献标签）：千帆学术（中文原词直搜）+ OpenAlex/DOAJ
        （英译 + language:zh 归并）；无 channel = 全部已注册适配器不过滤。
        """
        query = ((req or {}).get("query") or "").strip()
        query_en = ((req or {}).get("query_en") or "").strip()
        max_results = int((req or {}).get("max_results") or 8)
        channel = (req or {}).get("channel") or None
        sources = (req or {}).get("sources") or None
        if not query and not query_en:
            raise HTTPException(400, "缺少 query")
        from m7_websearch.config import web_search_available

        if not web_search_available():
            return {"ok": False, "enabled": False, "message": _WEB_SEARCH_DISABLED_MSG}

        # T7 中文通道：源集合 + 语言过滤 + 中文原词（千帆直搜用）
        lang = None
        dedup = False
        if channel == "zh":
            sources = sources or ["qianfan_scholar", "openalex", "doaj"]
            lang = "zh"
        elif channel == "intl":
            # T6 国际通道：OpenAlex/DOAJ/arXiv 扇出 + DOI/标题去重合并（第十六条）
            sources = sources or ["openalex", "doaj", "arxiv"]
            dedup = True

        translation = None
        if not query_en:
            from m7_websearch.translate import translate_query

            translation = translate_query(query)
            query_en = translation["translated"]

        from m7_websearch.registry import dedup_results, run_search

        results = run_search(query_en, max_results, sources, query_orig=query or None, lang=lang)
        if dedup:
            results = dedup_results(results)
        import datetime

        search_date = datetime.date.today().isoformat()
        src_names = sorted({r.source for r in results})
        return {
            "ok": True,
            "enabled": True,
            "channel": channel,
            "query": query,
            "query_en": query_en,
            "translation_method": (translation or {}).get("method", "none"),
            "translation_hint": (translation or {}).get("hint", ""),
            "results": [r.to_dict() for r in results],
            "sources": src_names,
            "count": len(results),
            # A/C 4：空结果非静默空白——前端据此提示换关键词
            "empty_hint": "" if results else "未检索到相关结果，建议更换关键词（试试更具体的术语 / 作者名 / 期刊名）",
            "search_date": search_date,
            "disclaimer": f"结果来自联网检索源（{', '.join(src_names) or '无命中'}），检索于 {search_date}，仅供文献发现，引用前请回原文核对。",
        }

    # ---- T8：OA 一键下载入库（蓝图第十一/十四条：仅对 OA 命中开放）----
    @app.post("/web/oa_ingest")
    def web_oa_ingest(req: dict):
        """OA 命中结果一键下载入库（轻量直入 M4，2026-08-12 用户拍板，同 T1 口径）。

        流程：门控（完全本地不可用）→ U4 download_file 流式下载（50MB 上限）→
        %PDF magic 校验（非 PDF 拒绝）→ fitz 文本层提取（空 = 扫描件拒绝）→
        内容哈希判重（duplicate 直接返回）→ md 落 03_知识库原文（文件名与
        sidecar.source_file 对齐，保 M4 匹配）→ sidecar 写 bibliography
        （含来源声明：来源/oa_url/检索日期，A/C 3）→ 原件归档 04_已入库归档 →
        M4 watcher 异步入库。失败全清理，不产生半成品（A/C 4）。
        """
        url = ((req or {}).get("url") or "").strip()
        title = ((req or {}).get("title") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            raise HTTPException(400, "缺少合法的下载链接 url")
        if not title:
            raise HTTPException(400, "缺少 title")
        from m7_websearch.config import web_search_available

        if not web_search_available():
            return {"ok": False, "enabled": False, "message": _WEB_SEARCH_DISABLED_MSG}

        source = ((req or {}).get("source") or "").strip()
        oa_search_date = ((req or {}).get("search_date") or "").strip()

        # 下载到 .upload_tmp（U4 download_file：流式 + 上限 + UA + 失败归一）
        tmp_dir = root / ".upload_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_name = compute_safe_filename(f"{title}.pdf")
        tmp_pdf = tmp_dir / safe_name
        from pipeline_core.http_client import download_file

        res = download_file(url, tmp_pdf, timeout=60)
        if not res.ok and res.status is None:
            # 网络层瞬时失败（本机代理 10054 重置偶发，实测重试即通）重试一次；HTTP 错误码不重试
            res = download_file(url, tmp_pdf, timeout=60)
        if not res.ok:
            raise HTTPException(502, f"OA 全文下载失败：{res.error}")

        # 校验 + 提取（失败清理 tmp，不落 sidecar/md 半成品）
        try:
            head = tmp_pdf.read_bytes()[:5]
            if head != b"%PDF-":
                # 落地页 HTML → 页内发现 PDF 直链再试（DOAJ 中文刊常见形态，
                # 2026-08-13 走查驱动增强；最多试 3 个候选，仍不行才拒绝）
                from m7_websearch.oa_pdf import extract_pdf_candidates, looks_like_html

                raw = tmp_pdf.read_bytes()
                candidates = extract_pdf_candidates(raw, url) if looks_like_html(raw) else []
                got = False
                for cand in candidates[:3]:
                    logger.info(f"[oa_ingest] 落地页发现 PDF 候选，尝试: {cand}")
                    res2 = download_file(cand, tmp_pdf, timeout=60)
                    if not res2.ok and res2.status is None:
                        res2 = download_file(cand, tmp_pdf, timeout=60)  # 瞬时重置重试一次
                    if res2.ok and tmp_pdf.read_bytes()[:5] == b"%PDF-":
                        got = True
                        break
                if not got:
                    raise HTTPException(
                        422,
                        "链接指向出版商落地页而非 PDF 直链，页内也未发现可用 PDF"
                        "（可能需登录或触发反爬），未入库",
                    )
            import fitz

            doc = fitz.open(str(tmp_pdf))
            try:
                page_count = len(doc)
                # 逐页带 <!-- page N --> 标注拼接（对齐 M2 ocr.md 格式；
                # 缺标注速览卡页码退化为全 1——2026-08-13 走查发现）
                pages_text = [p.get_text("text") for p in doc]
            finally:
                doc.close()
            if not "".join(pages_text).strip():
                raise HTTPException(422, "该 PDF 为扫描件（无文本层），本轮不接入库；请自行获取后走文献库通道")
            text = "\n\n".join(f"<!-- page {i + 1} -->\n{t}" for i, t in enumerate(pages_text))

            # 内容哈希判重（sidecar 已存在 = 已入库/入队）
            from pipeline_core.doc_id import compute_doc_id

            doc_id = compute_doc_id(tmp_pdf)
            sidecar_dir = root / "sidecar"
            if (sidecar_dir / f"{doc_id}.json").exists():
                return {
                    "ok": True, "ingested": False, "duplicate": True, "doc_id": doc_id,
                    "message": "该文献已在库（内容哈希命中），未重复入库",
                }

            # md 落 03 队列（stem 与 source_file 对齐：M4 按此匹配 sidecar）
            stem = Path(safe_name).stem
            md = root / "03_知识库原文" / f"{stem}.md"
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text(text, encoding="utf-8")

            # sidecar：bibliography 承载元数据 + 来源声明（A/C 3）
            from pipeline_core.sidecar import init_sidecar, update_sidecar_field

            init_sidecar(sidecar_dir, doc_id, safe_name, page_count=page_count, translate_flag=False)
            update_sidecar_field(
                sidecar_dir, doc_id,
                bibliography={
                    "title": title,
                    "authors": (req or {}).get("authors") or [],
                    "journal": (req or {}).get("journal") or None,
                    "year": (req or {}).get("year") or None,
                    "doi": (req or {}).get("doi") or None,
                    "type": "J",
                    "origin": "oa_download",
                    "oa_source": source,
                    "oa_url": url,
                    "oa_search_date": oa_search_date,
                },
            )

            # 原件归档（对齐 M2 归档结构：04_已入库归档/YYYY-MM/原始文件/）
            import datetime
            import shutil

            archive_dir = root / "04_已入库归档" / datetime.date.today().strftime("%Y-%m") / "原始文件"
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_pdf), str(archive_dir / safe_name))

            # Q5（2026-08-13 用户拍板）：OA 下载即后台预生成速览卡——与入库共用同一份
            # 提取文本（不等 M4 索引完成），打开速览卡时多已就绪。后端 stub/完全本地 →
            # 跳过不影响入库（用户可后续手动生成）。
            auto_digest = False
            try:
                from llm.providers import active_provider

                pv = active_provider()
                if pv is not None and getattr(pv, "name", "") != "stub":
                    with digest_lock:
                        job = {
                            "status": "processing", "total": 0, "done": 0,
                            "cancel": False, "thread": None, "error": None,
                        }
                        digest_jobs[doc_id] = job
                    t = threading.Thread(target=_run_digest, args=(doc_id, text), daemon=True)
                    job["thread"] = t
                    t.start()
                    auto_digest = True
            except Exception as e:
                logger.warning(f"[oa_ingest] 速览卡预生成启动失败（跳过不影响入库）: {e}")

            logger.info(f"[oa_ingest] 入库排队: {title!r} ← {source} doc_id={doc_id} auto_digest={auto_digest}")
            return {
                "ok": True, "ingested": True, "queued": True, "doc_id": doc_id,
                "source": source,
                "search_date": oa_search_date,
                "auto_digest": auto_digest,
                "message": f"已下载并提交入库（来源：{source}，检索于 {oa_search_date}），数秒后本地语义检索可命中"
                + ("；速览卡后台生成中" if auto_digest else ""),
            }
        finally:
            tmp_pdf.unlink(missing_ok=True)  # 成功时文件已 move 走，失败时清理半成品

    # ---- Q3（2026-08-13 走查反馈，用户定案）：检索结果收藏清单（待获取）----
    # 大多数检索结果不能一键入库（OA 直连有限），用户走自有渠道（知网会员等）获取——
    # 清单承接「找到 → 收藏 → 自行获取 → 回流入库」闭环；清单名带检索关键词；导出走 CSV。
    def _collections_dir() -> Path:
        d = root / "collections"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _safe_collection_name(name: str) -> str:
        """清单名净化（Windows 非法字符 + 连续点剔除 + 限长）；空名回退 untitled。"""
        import re as _re

        safe = _re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())
        safe = _re.sub(r"\.{2,}", "_", safe).strip(". ")  # 连续点（穿越残留）一并剔除
        return (safe[:60] or "untitled")

    def _collection_file(name: str) -> Path:
        return _collections_dir() / f"{_safe_collection_name(name)}.json"

    @app.post("/web/collections")
    def create_collection(req: dict):
        """保存收藏清单：勾选项 + 检索上下文。清单名 = 检索关键词_时间戳（重名加序号）。"""
        items = (req or {}).get("items") or []
        if not items:
            raise HTTPException(400, "勾选项为空")
        import datetime

        query = ((req or {}).get("query") or "").strip()
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        base = _safe_collection_name(f"{query}_{ts}" if query else f"清单_{ts}")
        name = base
        n = 2
        while _collection_file(name).exists():
            name = f"{base}_{n}"
            n += 1
        data = {
            "name": name,
            "query": query,
            "channel": (req or {}).get("channel") or "",
            "search_date": (req or {}).get("search_date") or "",
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "items": items,
        }
        import json

        _collection_file(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[collections] 新建清单 {name!r}（{len(items)} 条，query={query!r}）")
        return {"ok": True, "name": name, "count": len(items)}

    @app.get("/web/collections")
    def list_collections():
        """清单列表（新→旧）。"""
        import json

        out = []
        for f in _collections_dir().glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append({
                    "name": d.get("name") or f.stem,
                    "query": d.get("query", ""),
                    "channel": d.get("channel", ""),
                    "created_at": d.get("created_at", ""),
                    "count": len(d.get("items") or []),
                })
            except Exception:
                continue  # 单文件损坏不拖垮列表
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return {"ok": True, "collections": out}

    @app.get("/web/collections/{name}")
    def get_collection(name: str):
        import json

        f = _collection_file(name)
        if not f.is_file():
            raise HTTPException(404, f"清单不存在: {name}")
        return json.loads(f.read_text(encoding="utf-8"))

    @app.delete("/web/collections/{name}")
    def delete_collection(name: str):
        """删除清单（回收站语义，P3 红线：移入 _trash 不物理删除）。"""
        import datetime
        import shutil

        f = _collection_file(name)
        if not f.is_file():
            raise HTTPException(404, f"清单不存在: {name}")
        trash = _collections_dir() / "_trash"
        trash.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.move(str(f), str(trash / f"{ts}_{f.name}"))
        return {"ok": True, "deleted": name}

    @app.post("/web/collections/{name}/export")
    def export_collection(name: str):
        """导出 CSV（UTF-8 BOM：Excel/WPS 中文不乱码，编码纪律 6.3）到 exports/ 目录。"""
        import csv
        import json

        f = _collection_file(name)
        if not f.is_file():
            raise HTTPException(404, f"清单不存在: {name}")
        data = json.loads(f.read_text(encoding="utf-8"))
        exports = root / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        out = exports / f"{_safe_collection_name(name)}.csv"
        with open(out, "w", encoding="utf-8-sig", newline="") as fp:
            w = csv.writer(fp)
            w.writerow(["标题", "作者", "期刊", "年份", "DOI", "来源", "详情页链接", "OA链接", "检索关键词", "检索日期"])
            for it in data.get("items") or []:
                w.writerow([
                    it.get("title") or "",
                    "; ".join(it.get("authors") or []),
                    it.get("journal") or "",
                    it.get("year") or "",
                    it.get("doi") or "",
                    it.get("source") or "",
                    it.get("url") or "",
                    it.get("oa_url") or "",
                    data.get("query", ""),
                    data.get("search_date", ""),
                ])
        logger.info(f"[collections] 导出 CSV: {out.name}（{len(data.get('items') or [])} 条）")
        return {"ok": True, "file": out.name, "path": str(out)}

    # ---- Web UI 路由 ----
    @app.get("/docs")
    def list_docs(offset: int = 0, limit: int = 20, q: str = ""):
        sidecar_dir = root / "sidecar"
        if not sidecar_dir.exists():
            return []
        # Q2（2026-08-13 走查反馈）：被引用标注 = 各项目 bibliography.csl.json 已登记的 doc_id 集合
        import json
        cited_ids: set = set()
        papers_dir = root / "papers"
        if papers_dir.is_dir():
            for bf in papers_dir.glob("*/bibliography.csl.json"):
                try:
                    for it in (json.loads(bf.read_text(encoding="utf-8")).get("items") or []):
                        if it.get("doc_id"):
                            cited_ids.add(it["doc_id"])
                except Exception:
                    continue  # 单项目清单损坏不拖垮列表
        docs = []
        for f in sorted(sidecar_dir.glob("*.json")):
            # 跳过速览卡缓存（*_digest.json 非文档 sidecar）
            if f.stem.endswith("_digest"):
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            bib = data.get("bibliography") or {}
            docs.append({
                "doc_id": data.get("doc_id", ""),
                "source_file": data.get("source_file", ""),
                # Q2：轻量直入（T1/T8）的 title 在 bibliography 里，顶层 title 为 None——回退取 bib
                "title": data.get("title") or bib.get("title"),
                "authors": data.get("authors"),
                "year": data.get("year"),
                "ingested_at": data.get("ingested_at", ""),
                "metadata_status": data.get("metadata_status", "pending"),
                # Q2：入库来源（oa_download | upload）+ 被项目引用标注
                "origin": bib.get("origin") or "upload",
                "cited": data.get("doc_id", "") in cited_ids,
            })
        if q:
            docs = [d for d in docs if q.lower() in str(d).lower()]
        return docs[offset:offset + limit]

    @app.get("/docs/{doc_id}")
    def get_doc_detail(doc_id: str):
        try:
            from pipeline_core.sidecar import read_sidecar
            sc = read_sidecar(root / "sidecar", doc_id)
            import dataclasses
            return dataclasses.asdict(sc)
        except Exception as e:
            raise HTTPException(404, f"文档未找到: {e}")

    # ---- P3.5-1 第 3 步：速览卡（思想层提炼，版权红线：不做原文浓缩）----
    def _find_doc_md(doc_id: str) -> Path | None:
        """定位已入库文档的原文 md：优先 03_知识库原文/<doc_id>.md，其次归档目录按 source_file stem 匹配。"""
        direct = root / "03_知识库原文" / f"{doc_id}.md"
        if direct.exists():
            return direct
        try:
            from pipeline_core.sidecar import read_sidecar
            sc = read_sidecar(root / "sidecar", doc_id)
            stem = Path(sc.source_file).stem
        except Exception:
            return None
        arch = root / "04_已入库归档"
        if arch.exists():
            for f in sorted(arch.rglob("*.md")):
                fstem = f.stem.lstrip("[T]")
                if fstem.startswith(stem) and ".ocr" in fstem:
                    return f
                if fstem == stem:
                    return f
        return None

    # 速览卡生成任务表（doc_id → job）：后台线程生成 + 轮询进度
    # job = {status: processing|done|cancelled|error, total, done, cancel: bool, thread, error}
    digest_jobs: dict[str, dict] = {}
    digest_lock = threading.Lock()

    def _run_digest(doc_id: str, text: str) -> None:
        job = digest_jobs.get(doc_id)
        if job is None:
            return
        try:
            from llm.providers import active_provider
            from m6_digest.digest_skill import make_digest
            # 快照 provider：生成期间用户切换后端 → 本线程继续用旧 provider 完成，结果不被混
            provider = active_provider()

            def on_progress(done: int, total: int) -> None:
                job["done"] = done
                job["total"] = total

            card = make_digest(text, provider, on_progress)
            if job["cancel"]:
                job["status"] = "cancelled"
                return
            if not isinstance(card, dict):
                card = {}
            card["doc_id"] = doc_id
            card["source"] = "fresh"
            # 顺带用当前后端重提取引用信息（用户需求：入库时本地模型提取有偏差，
            # 切云端后重生成速览卡时一并刷新）。写回 sidecar 并降回 pending——
            # 不静默顶替已确认值，用户核对后重新确认。
            if provider is not None:
                try:
                    fp_path = root / "sidecar" / f"{doc_id}_firstpage.txt"
                    if fp_path.exists():
                        from m6_cite.cite_skill import firstpage_to_biblio, set_provider
                        set_provider(provider)
                        draft = firstpage_to_biblio(
                            fp_path.read_text(encoding="utf-8"), None, "GB/T 7714-2015"
                        )
                        if draft and draft.biblio and draft.biblio.get("title"):
                            from pipeline_core.sidecar import update_sidecar_field
                            update_sidecar_field(
                                root / "sidecar", doc_id,
                                bibliography=draft.biblio,
                                metadata_status="pending",
                            )
                            card["cite_updated"] = True
                            card["cite"] = {
                                "draft": draft.biblio,
                                "formatted": draft.formatted,
                                "confirmed": False,
                            }
                except Exception as e:
                    logger.warning(f"速览卡生成时重提取引用失败 {doc_id}: {e}")
            _digest_write(root, doc_id, card)
            job["status"] = "done"
            job["done"] = job["total"]
        except Exception as e:  # noqa: BLE001
            logger.exception(f"速览卡生成失败 {doc_id}")
            job["status"] = "error"
            job["error"] = str(e)

    @app.get("/docs/{doc_id}/digest")
    def doc_digest(doc_id: str, force: int = 0):
        if not force:
            d = _digest_read(root, doc_id)
            if d is not None:
                d["source"] = "cached"
                return d
        md = _find_doc_md(doc_id)
        if md is None:
            raise HTTPException(404, "该文档尚未完成入库，无法生成速览卡（需先经管线处理进入知识库原文）")
        text = md.read_text(encoding="utf-8")
        if len(text.strip()) < 200:
            raise HTTPException(404, "文档文本过短，无法生成速览卡")
        with digest_lock:
            job = digest_jobs.get(doc_id)
            if job and job["status"] == "processing":
                # 已在生成中：不重复启动，返回当前进度
                return {"status": "processing", "total": job["total"], "done": job["done"]}
            if force and job and job["status"] == "processing":
                job["cancel"] = True  # 强制重生成 → 取消进行中任务，下面起新线程
            job = {
                "status": "processing", "total": 0, "done": 0,
                "cancel": False, "thread": None, "error": None,
            }
            digest_jobs[doc_id] = job
            t = threading.Thread(target=_run_digest, args=(doc_id, text), daemon=True)
            job["thread"] = t
            t.start()
        return {"status": "processing", "total": 0, "done": 0}

    @app.get("/docs/{doc_id}/digest/progress")
    def doc_digest_progress(doc_id: str):
        """速览卡生成进度轮询：{status, total, done, error?}。"""
        job = digest_jobs.get(doc_id)
        if job is None:
            if _digest_read(root, doc_id) is not None:
                return {"status": "done", "total": 0, "done": 0}
            return {"status": "idle", "total": 0, "done": 0}
        resp = {
            "status": job["status"],
            "total": job.get("total", 0),
            "done": job.get("done", 0),
        }
        if job.get("error"):
            resp["error"] = job["error"]
        return resp

    @app.get("/docs/{doc_id}/file")
    def get_doc_file(doc_id: str):
        # 从 sidecar 获取 source_file，然后查找原始 PDF
        from pipeline_core.sidecar import read_sidecar
        try:
            sc = read_sidecar(root / "sidecar", doc_id)
        except Exception:
            raise HTTPException(404, "文档未找到")

        source_name = sc.source_file
        source_stem = Path(source_name).stem
        # 归档文件名可能带 [T] 前缀（需翻译标记，M0 写入后 M3 摘除），匹配时忽略
        source_stem_clean = source_stem.lstrip("[T]")
        for qname in ["04_已入库归档", "00_待处理", "01_OCR队列", "02_翻译队列"]:
            qdir = root / qname
            if qdir.exists():
                for f in qdir.rglob("*.pdf"):
                    cand = f.stem.lstrip("[T]")
                    # 归档文件名带时间戳后缀（source_stem_YYYYMMDDHHMMSS.pdf），按 stem 前缀匹配
                    if cand == source_stem_clean or cand.startswith(source_stem_clean + "_") or f.name == source_name:
                        # RFC 5987：中文文件名不能直接进 HTTP 头（latin-1），用 filename* 百分号编码
                        from urllib.parse import quote
                        fname_ascii = f.name.encode("ascii", "ignore").decode() or "document.pdf"
                        disposition = f"inline; filename=\"{fname_ascii}\"; filename*=UTF-8''{quote(f.name)}"
                        # FileResponse 自动管理文件句柄（StreamingResponse(open()) 惰性读会泄漏句柄，曾致文件删不掉）
                        from fastapi.responses import FileResponse
                        return FileResponse(
                            str(f),
                            media_type="application/pdf",
                            headers={"Content-Disposition": disposition},
                        )
        raise HTTPException(404, "原始PDF未找到")

    @app.post("/docs/{doc_id}/bibliography")
    def confirm_bibliography(doc_id: str, req: BibliographyRequest):
        from pipeline_core.sidecar import update_sidecar_field
        update_sidecar_field(
            root / "sidecar", doc_id,
            bibliography=req.bibliography,
            metadata_status="confirmed",
        )
        return {"ok": True}

    # 简易 import_list（内存存储）
    # 现状（R3 盘点）：_import_list 无任何追加路径，GET 恒返空表；
    # 保留端点是因为 webui 导入页（js/views/import.js → api.getImportList）
    # 每次渲染都会调用它，删除会导致前端报错。"待导入清单"卡片因此永不显示。
    _import_list: list[dict] = []

    @app.get("/import-list")
    def get_import_list():
        return [{"idx": i, **item} for i, item in enumerate(_import_list)]

    @app.delete("/import-list/{idx}")
    def delete_import_item(idx: int):
        if 0 <= idx < len(_import_list):
            _import_list.pop(idx)
        return {"ok": True}

    # ---- Web UI（SPA）托管：页面免鉴权，API token 注入页面供其调用 API ----
    webui_dir = _find_webui_dir()
    if webui_dir is not None:
        index_html = webui_dir / "index.html"
        if index_html.exists():
            @app.get("/", response_class=HTMLResponse)
            def serve_index():
                html = index_html.read_text(encoding="utf-8")
                inject = f'<script>window.__KB_TOKEN__ = "{_api_token}";</script>'
                return html.replace("<head>", f"<head>\n{inject}", 1)

        for sub in ("css", "js", "assets", "vendor"):
            subdir = webui_dir / sub
            if subdir.exists():
                app.mount(f"/{sub}", StaticFiles(directory=subdir), name=f"webui-{sub}")
    else:
        logger.warning("webui 目录未找到，/ 不提供 Web UI（API 仍可用）")

    return app


def _find_webui_dir() -> Path | None:
    """定位 webui 目录：exe（_MEIPASS）> 包内 webui/ > 开发态 pack-P6-delivery/webui。"""
    import sys
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "webui")
    here = Path(__file__).resolve()
    candidates += [
        here.parent / "webui",                                  # server/webui
        here.parent.parent / "webui",                           # pack-P5-delivery/webui
        here.parent.parent.parent / "pack-P6-delivery" / "webui",  # 开发态
    ]
    for c in candidates:
        if c.exists() and (c / "index.html").exists():
            return c
    return None


def _reset_sidecar_retry(root: Path, filename: str):
    """按文件名查找 sidecar 并清零 retry_count。

    2026-08-16 会话 9 修复：异常队列文件名带入库时间戳（{stem}_{YYYYMMDDHHMMSS}.pdf）
    而 sidecar.source_file 为原名——原「子串互含」判断对时间戳后缀失效 → retry_count
    永不归零 → M2 直接跳过（retry_count≥上限）→ 重试无效。先剥离时间戳段再比较。
    """
    import re as _re

    from pipeline_core.sidecar import read_sidecar, update_sidecar_field
    sidecar_dir = root / "sidecar"
    if not sidecar_dir.exists():
        return
    # 剥时间戳：{stem}_20260816222721.pdf → {stem}.pdf（文件名带时间戳时）
    base = _re.sub(r"_\d{14}(?=\.\w+$)", "", filename)
    for scf in sidecar_dir.glob("*.json"):
        try:
            sc = read_sidecar(sidecar_dir, scf.stem)
            src = sc.source_file or ""
            if (src and (src in filename or filename in src or src in base or base in src)):
                update_sidecar_field(sidecar_dir, sc.doc_id, retry_count=0)
                return
        except Exception:
            continue
