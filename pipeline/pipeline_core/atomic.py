"""原子文件移动 + 原子文本写。

取件先改名 .processing → 完成才移送目标目录 → 摘除 .processing 后缀。
启动时扫描各队列 .processing 残留自动回收重跑。

atomic_write（S4 收编）：原 sidecar/page_plan/workbench_state 三处逐字重复的
_atomic_write 统一入口——先写同目录 tmp 再 os.replace，崩溃不留半截文件。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write(path: Path, content: str) -> None:
    """原子写文本：先写同目录 tmp（UTF-8）再 os.replace。

    tmp 与目标同目录（同盘），replace 才原子；异常时清理 tmp 后原样抛出。

    Windows 并发坑（2026-08-10 真机走查发现）：os.replace 在目标被其他线程/进程
    以 Python open()（无 FILE_SHARE_DELETE 共享）打开读取时会 WinError 5 拒绝访问——
    uvicorn 线程池下并发 paper_detail 各触发一次 ensure 写即复现。
    对策：PermissionError 短暂退避重试（读者毫秒级释放），其余异常原样抛出。
    """
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".atomic_", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        last_err: PermissionError | None = None
        for _ in range(10):  # 最多 ~0.5s：10 × 50ms
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.05)
        raise last_err  # type: ignore[misc]
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class AtomicMoveError(Exception):
    """原子移动失败。"""


def _is_same_disk(src: Path, dst_dir: Path) -> bool:
    """判断 src 和 dst_dir 是否在同一磁盘（Windows 兼容）。"""
    try:
        return os.stat(src).st_dev == os.stat(dst_dir).st_dev
    except OSError:
        return False


def _safe_replace(src: Path, dst: Path) -> None:
    """os.replace 封装，Windows 文件锁降级为 copy+delete。"""
    try:
        os.replace(src, dst)
    except OSError:
        # Windows: 文件被占用时降级为复制+删除
        try:
            import shutil
            shutil.copy2(src, dst)
            try:
                src.unlink()
            except OSError:
                pass  # 源文件删不掉就算了，下次启动扫描会跳过
        except OSError as e2:
            raise AtomicMoveError(f"替换失败: {src} -> {dst}: {e2}") from e2


def atomic_move(src: Path, dst_dir: Path, new_name: str | None = None) -> Path:
    """取件→改名 .processing→移送目标目录→摘除后缀。

    步骤：
    1) src rename 为 <src>.processing
    2) 移送 dst_dir
    3) 摘除 .processing 后缀

    返回最终文件路径。
    """
    if not src.exists():
        raise AtomicMoveError(f"源文件不存在: {src}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    # 1) 改名 .processing
    processing_path = src.with_suffix(src.suffix + ".processing")
    _safe_replace(src, processing_path)

    # 2) 移送目标目录
    final_name = new_name if new_name else processing_path.name
    final_name = final_name.removesuffix(".processing")
    dest = dst_dir / final_name

    try:
        if _is_same_disk(processing_path, dst_dir):
            _safe_replace(processing_path, dst_dir / processing_path.name)
        else:
            shutil.move(str(processing_path), str(dst_dir / processing_path.name))
    except OSError as e:
        # 尝试回滚
        try:
            os.replace(processing_path, src)
        except OSError:
            pass
        raise AtomicMoveError(f"移送失败: {processing_path} -> {dst_dir}: {e}") from e

    # 3) 摘除 .processing 后缀
    intermediate = dst_dir / processing_path.name
    _safe_replace(intermediate, dest)

    logger.info(f"原子移动完成: {src.name} -> {dest}")
    return dest


def recover_orphaned_processing(root: Path) -> list[Path]:
    """启动时扫描各队列 *.processing 残留，摘除后缀恢复原名。

    返回恢复的文件路径列表。
    """
    queue_dirs = [
        root / "00_待处理",
        root / "01_OCR队列",
        root / "02_翻译队列",
        root / "03_知识库原文",
        root / "99_异常",
    ]

    recovered: list[Path] = []
    for qdir in queue_dirs:
        if not qdir.exists():
            continue
        for f in qdir.iterdir():
            if f.is_file() and f.suffix == ".processing":
                restored = f.with_suffix("")  # 摘除 .processing
                try:
                    os.replace(f, restored)
                    recovered.append(restored)
                    logger.warning(f"残留 .processing 已恢复: {restored}")
                except OSError as e:
                    logger.error(f"恢复 .processing 残留失败: {f}: {e}")

    return recovered
