"""M2 OCR — 消费页计划，执行 OCR/extract/keep_asset。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _short_path_win(p: str) -> str:
    """Windows 8.3 短路径（纯 ASCII）。

    Paddle inference C++ 层用窄字符 API 打开模型文件，含中文（如「ZCode项目」）的
    绝对路径会按 ANSI 代码页误读 → NotFound。GetShortPathNameW 返回 ASCII 短路径绕开。
    失败/无 8.3 支持时返回原路径（由调用方回退）。
    """
    import ctypes
    from ctypes import wintypes

    try:
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetShortPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(512)
        n = GetShortPathNameW(str(p), buf, 512)
        return buf.value if n else str(p)
    except Exception:
        return str(p)


class OCREngine:
    """PaddleOCR 封装。"""

    def __init__(self, config: dict | None = None):
        self._ocr = None
        self._config = config or {}

    def _load(self):
        if self._ocr is None:
            # 自包含（M1 切片 2 修订）：PaddleOCR 模型缓存指向项目内 tools/paddlex，
            # 不依赖用户目录 ~/.paddlex；环境变量已设置则尊重外部覆盖。
            if "PADDLE_PDX_CACHE_HOME" not in os.environ:
                tools_paddlex = Path(__file__).resolve().parents[2] / "tools" / "paddlex"
                if (tools_paddlex / "official_models").is_dir():
                    cand = str(tools_paddlex)
                    # 2026-08-16 会话 9 修复：Paddle inference C++ 窄字符 API 无法打开
                    # 含非 ASCII（中文「项目」）的绝对路径 → OCR 全部失败入 99_异常。
                    # Windows 下转 8.3 短路径（纯 ASCII）绕开；非 Windows 或转换失败回退原路径。
                    if os.name == "nt" and any(ord(ch) > 127 for ch in cand):
                        cand = _short_path_win(cand) or cand
                    os.environ["PADDLE_PDX_CACHE_HOME"] = cand
            try:
                # 2026-08-16 会话 9 修复：oneDNN CPU 算子在某些扫描件（GB/T 国标等）
                # 推理时 C++ segfault（日志 ReduceMean 后进程静默退出，无 Python traceback）。
                # 禁用 oneDNN（FLAGS_use_mkldnn=0）后实测 21 页全部成功（150DPI 每页 15-57s）。
                # 需在 import paddle 之前设置环境变量。
                if "FLAGS_use_mkldnn" not in os.environ:
                    os.environ["FLAGS_use_mkldnn"] = "0"
                from paddleocr import PaddleOCR
            except ImportError:
                raise RuntimeError("PaddleOCR 未安装。pip install paddleocr")
            try:
                # PaddleOCR 3.x：构造签名重构（无 use_gpu/show_log）
                self._ocr = PaddleOCR(
                    lang="ch",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except TypeError:
                # PaddleOCR 2.x 兼容路径
                self._ocr = PaddleOCR(lang="ch", use_gpu=False, show_log=False)
            except Exception:
                # 调试：打包排障期打印完整异常链（paddlex 包装会吞真实缺失项）
                import traceback
                logger.error("PaddleOCR 初始化完整异常链:\n%s", traceback.format_exc())
                raise

    @staticmethod
    def _extract(results) -> tuple[list[str], list[float]]:
        """统一 3.x predict 与 2.x ocr 的返回结构，抽出 (文本行, 置信度)。"""
        if not results:
            return [], []
        first = results[0]
        # 3.x predict: [{'rec_texts': [...], 'rec_scores': [...]}, ...]
        if isinstance(first, dict) and "rec_texts" in first:
            texts, confs = [], []
            for r in results:
                texts.extend(r.get("rec_texts", []))
                confs.extend(r.get("rec_scores", []))
            return texts, confs
        # 2.x ocr: [[ [bbox, (text, score)], ... ]]
        if not first:
            return [], []
        texts = [line[1][0] for line in first]
        confs = [line[1][1] for line in first]
        return texts, confs

    def _run(self, image) -> tuple[list[str], list[float]]:
        import numpy as np
        self._load()
        if not isinstance(image, np.ndarray):
            image = np.array(image)
        if hasattr(self._ocr, "predict"):
            results = self._ocr.predict(image)
        else:
            results = self._ocr.ocr(image, cls=False)
        return self._extract(results)

    def ocr_page(self, image) -> str:
        """对单页图像 OCR，返回文本。"""
        texts, _ = self._run(image)
        return "\n".join(texts)

    def ocr_page_with_confidence(self, image) -> tuple[str, float]:
        """OCR 并返回页均置信度。"""
        texts, confs = self._run(image)
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        return "\n".join(texts), avg_conf


def render_page_for_ocr(doc, page_num: int, dpi: int = 300):
    """将指定页渲染为 300DPI numpy 图像数组。"""
    page = doc[page_num - 1]
    pix = page.get_pixmap(dpi=dpi)
    import numpy as np
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )


def build_ocr_md(pages_md: list[tuple[int, str]], source_file: str) -> str:
    """页序拼接 MD，每页间插入 <!-- page N -->。"""
    parts = []
    for page_num, text in sorted(pages_md):
        parts.append(f"<!-- page {page_num} -->\n{text}")
    return "\n\n".join(parts)
