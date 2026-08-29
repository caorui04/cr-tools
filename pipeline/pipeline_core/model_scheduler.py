"""模型/资源调度器 — 内存预算控制 + 按需加载 + 孤儿清理 + M2 worker 互斥。

设计目标（2026-08-01，用户场景评审结论）：
1. 检索（8083 嵌入）日常高频 → 常驻，代价最小（0.7GB）
2. 管线处理是"批量提交闲时静默完成" → M2 worker 按需（01 队列积压拉起，
   处理完保活超时退出），一次恒 1 个（互斥锁防叠加）
3. 翻译/摘要（8081/8082）批量入库后仅零星新文档 → 按需/懒加载 + 保活停机
4. 内存总预算硬上限：启动前检查，超预算按优先级回收空闲模型，
   预算仍不足则拒绝启动（文档留队列等下次 tick），杜绝无限叠加
5. 孤儿清理：管线启动 + 周期扫描，kill 命令行含 "--ocr-worker <root>" 的
   孤儿 M2 worker（用户关 UI/主进程被杀后遗留），防 1.7GB × N 叠加

与桌面应用协作：spawn 前端口查重——端口已在线（桌面 auto_start 拉起）视为
外部管理，不重复拉起、不回收；仅回收"本调度器自己拉起且空闲"的模型。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows CREATE_NO_WINDOW
_CREATE_NO_WINDOW = 0x08000000


@dataclass
class ModelSlot:
    """模型槽位：预算估算 + 优先级 + 生命周期 + 进程管理。"""

    slot_id: str            # "embedding" | "m2_worker" | "translate" | "citation"
    port: int               # 0 表示无端口（如 M2 worker 子进程）
    mem_mb: int             # 预算估算（实测：BGE-M3 700MB / worker 1700MB / Hy-MT2 4400MB / Qwen3B 2000MB）
    prio: int               # 0=常驻最高  1=中  2=低（回收顺序：先回收 prio 大的）
    mode: str               # "resident" | "ondemand" | "lazy"
    model_file: str = ""    # gguf 文件名（M2 worker 无）
    exe_path: str = ""      # llama-server.exe（M2 worker 无）
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _owned: bool = False    # 是否本调度器拉起（外部占用不算，不回收）
    _last_idle_at: float = 0.0

    def is_alive(self) -> bool:
        """端口模式探测端口；无端口模式探测进程。"""
        if self.port:
            return _port_in_use(self.port)
        return self._proc is not None and self._proc.poll() is None


# ---- 注册表（预算估算来自实测）----

def _build_registry(install_dir: Path) -> dict[str, ModelSlot]:
    llama_exe = install_dir / "llama" / "llama-server.exe"
    models_dir = install_dir / "models"
    return {
        "embedding": ModelSlot("embedding", 8083, 700, 0, "resident",
                               "bge-m3-Q8_0.gguf", str(llama_exe)),
        "m2_worker": ModelSlot("m2_worker", 0, 1700, 1, "ondemand"),
        "translate": ModelSlot("translate", 8081, 4400, 2, "ondemand",
                               "Hy-MT2-1.8B-Q8_0.gguf", str(llama_exe)),
        "citation": ModelSlot("citation", 8082, 2000, 2, "lazy",
                              "Qwen2.5-3B-Instruct-Q4_K_M.gguf", str(llama_exe)),
    }


def _port_in_use(port: int) -> bool:
    try:
        import socket
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class ModelScheduler:
    """内存预算调度器。线程安全（全局锁保护注册表）。"""

    def __init__(
        self,
        install_dir: Path,
        root: Path,
        budget_mb: int = 8000,
        idle_stop_s: float = 600.0,
    ):
        self._install_dir = install_dir
        self._root = root
        self._budget_mb = budget_mb
        self._idle_stop_s = idle_stop_s
        self._slots = _build_registry(install_dir)
        self._lock = threading.Lock()
        # 保活计时器：每 tick 调用 touch() 刷新
        self._activity = {sid: time.time() for sid in self._slots}

    # ---- 预算 ----

    def _owned_mem(self) -> int:
        """本调度器拉起且存活的模型占用（外部占用不计，不回收）。"""
        total = 0
        for s in self._slots.values():
            if s._owned and s.is_alive():
                total += s.mem_mb
        return total

    def _total_model_mem(self) -> int:
        """全量模型占用：owned + 外部探测在线（端口被桌面/用户拉起）。"""
        total = 0
        for s in self._slots.values():
            if s.is_alive():
                total += s.mem_mb
        return total

    def _ensure_budget(self, need_mb: int) -> bool:
        """预算检查：全量占用 + need ≤ 预算。超预算按 prio 回收 owned 空闲模型腾空间。

        预算判断用全量（含外部占用的端口探测），回收只动自己拉起的（owned）。
        """
        while self._total_model_mem() + need_mb > self._budget_mb:
            # 找最该回收的：prio 最大（最不重要）且非 resident、已空闲、owned
            victims = [
                s for s in self._slots.values()
                if s._owned and s.is_alive()
                and s.mode != "resident"
                and self._is_idle(s)
            ]
            if not victims:
                return False
            victims.sort(key=lambda s: (-s.prio, s.mem_mb))
            v = victims[0]
            logger.warning(
                f"调度器: 预算不足（全量占用 {self._total_model_mem()}MB + 需 {need_mb}MB > "
                f"预算 {self._budget_mb}MB），回收空闲模型 {v.slot_id}（{v.mem_mb}MB）"
            )
            self.stop(slot_id=v.slot_id)
        return True

    def _is_idle(self, slot: ModelSlot) -> bool:
        return (time.time() - self._activity.get(slot.slot_id, 0.0)) > self._idle_stop_s

    # ---- 拉起 ----

    def ensure(self, slot_id: str) -> bool:
        """按需拉起：预算检查 → 回收 → 启动/查重。已在线（含外部）返回 True。"""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot is None:
                return False
            if slot.is_alive():
                self._activity[slot_id] = time.time()
                return True
            if slot.mode == "resident":
                # 常驻模型（8083）：尝试拉起，失败告警
                return self._spawn(slot)
            if not self._ensure_budget(slot.mem_mb):
                logger.warning(
                    f"调度器: 预算不足，拒绝拉起 {slot_id}（{slot.mem_mb}MB），"
                    f"文档留在队列等待后续 tick"
                )
                return False
            return self._spawn(slot)

    def _spawn(self, slot: ModelSlot) -> bool:
        """启动 llama-server（复用桌面相同命令构造）。"""
        if not slot.port:
            return True  # M2 worker 不走此路径（由 dispatcher spawn）
        if not Path(slot.exe_path).exists():
            logger.error(f"调度器: llama-server 未找到: {slot.exe_path}")
            return False
        model_path = self._install_dir / "models" / slot.model_file
        if not model_path.exists():
            logger.error(f"调度器: 模型文件未找到: {model_path}")
            return False
        cmd = [
            slot.exe_path,
            "--model", str(model_path),
            "--port", str(slot.port),
            "--host", "127.0.0.1",
            "-ngl", "99",
        ]
        if slot.slot_id == "embedding":
            cmd += ["--embeddings", "-b", "4096", "-ub", "4096"]
        try:
            slot._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
            slot._owned = True
            self._activity[slot.slot_id] = time.time()
            logger.info(f"调度器: 拉起 {slot.slot_id} (port {slot.port})")
            return True
        except OSError as e:
            logger.error(f"调度器: 拉起 {slot.slot_id} 失败: {e}")
            return False

    # ---- 停机 ----

    def maybe_stop(self, slot_id: str, force: bool = False) -> None:
        """空闲保活超时自动停（仅停自己拉起的）。force=True 直接停。"""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot is None or not slot._owned or not slot.is_alive():
                return
            if force or self._is_idle(slot):
                self.stop(slot_id)

    def stop(self, slot_id: str) -> None:
        """停自己拉起的模型（taskkill /T 杀进程树）。"""
        slot = self._slots.get(slot_id)
        if slot is None or not slot._owned:
            return
        try:
            if slot._proc is not None and slot._proc.poll() is None:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(slot._proc.pid)],
                    capture_output=True, timeout=15,
                )
                slot._proc.wait(timeout=10)
            slot._proc = None
            slot._owned = False
            logger.info(f"调度器: 停止 {slot_id}")
        except Exception as e:
            logger.warning(f"调度器: 停止 {slot_id} 异常: {e}")

    def stop_all(self) -> None:
        """停机（主进程退出时调用）。"""
        for sid in list(self._slots):
            self.stop(sid)

    # ---- 孤儿清理 ----

    def reap_orphans(self) -> int:
        """杀孤儿 M2 worker：命令行含 --ocr-worker <root> 的进程。

        用户关 UI / 主进程被杀后，worker 子进程变孤儿继续跑（1.7GB + 12 核），
        重启管线若不清理会与新的 worker 叠加。

        2026-08-16 会话 9 修复：原实现杀掉全部 --ocr-worker（含正常运行的，
        其父进程=当前管线进程）——30s 周期巡检把长耗时 OCR worker 误杀 → M2 重试
        3 次后入 99_异常。改为按 ParentProcessId 判别：父进程非当前管线进程
        （真孤儿）才清理；正常 worker（父进程=本进程）保留。
        """
        root_str = str(self._root)
        root_match = root_str.replace("\\", "/")  # 命令行 wmic 输出用正斜杠
        killed = 0
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='kb-pipeline.exe' or name='python.exe'",
                 "get", "ProcessId,ParentProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            # csv 表头: Node,CommandLine,ParentProcessId,ProcessId（按表头定位列，避免顺序假设）
            col_pid = col_ppid = -1
            header_seen = False
            for line in out.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if not header_seen:
                    header_seen = True
                    cells = stripped.split(",")
                    for i, h in enumerate(cells):
                        if h.strip() == "ProcessId":
                            col_pid = i
                        elif h.strip() == "ParentProcessId":
                            col_ppid = i
                    continue
                if "--ocr-worker" not in stripped or root_match not in stripped.replace("\\", "/"):
                    continue
                cells = stripped.split(",")
                pid = cells[col_pid].strip() if 0 <= col_pid < len(cells) else ""
                ppid = cells[col_ppid].strip() if 0 <= col_ppid < len(cells) else ""
                if not pid.isdigit():
                    continue
                # 父进程 = 当前管线进程 → 正常 worker，保留；否则真孤儿 → 清理
                if ppid.isdigit() and int(ppid) == os.getpid():
                    continue
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", pid],
                        capture_output=True, timeout=15,
                    )
                    killed += 1
                    logger.warning(f"调度器: 清理孤儿 M2 worker PID={pid} (ppid={ppid})")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"调度器: 孤儿扫描异常: {e}")
        return killed

    # ---- M2 worker 互斥锁（模块级函数，dispatcher 直接使用）----

    @staticmethod
    def worker_lock_path_for(root: Path) -> Path:
        return root / "progress" / ".m2_worker.lock"

    @staticmethod
    def acquire_worker_lock(root: Path) -> bool:
        """M2 worker 互斥锁：已有活跃 worker 则拒绝（防 1.7GB 叠加）。"""
        lock_path = ModelScheduler.worker_lock_path_for(root)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.exists():
            try:
                pid = int(lock_path.read_text(encoding="utf-8").strip())
                if _pid_alive(pid):
                    return False  # 已有活跃 worker
            except (ValueError, OSError):
                pass  # 锁损坏/已死 → 接管
        try:
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except OSError as e:
            logger.warning(f"调度器: worker 锁写入失败: {e}")
            return True  # 写失败不阻塞（乐观放行）

    @staticmethod
    def release_worker_lock(root: Path) -> None:
        """释放 worker 锁（仅当锁内 PID 是自己）。"""
        lock_path = ModelScheduler.worker_lock_path_for(root)
        try:
            if lock_path.exists():
                pid = int(lock_path.read_text(encoding="utf-8").strip())
                if pid == os.getpid():
                    lock_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass

    @property
    def worker_lock_path(self) -> Path:
        return ModelScheduler.worker_lock_path_for(self._root)


def _pid_alive(pid: int) -> bool:
    """跨平台 PID 存活检测。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
