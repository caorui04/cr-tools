"""文档分类器 — 文档级 + 页级分类。

判定依据：C1 §8 阈值 + C2 §1 页级 action 规则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class DocClassification:
    composition: str  # "text" | "image" | "mixed"
    language: str     # "A" | "B" | "C"


@dataclass
class PageAction:
    page: int
    action: str          # "extract" | "ocr"
    images: list[dict]   # [{"bbox": [...], "area_ratio": 0.3, "action": "keep_asset"}]


def count_cjk_ratio(text: str) -> float:
    """统计文本中 CJK 字符占比。"""
    if not text:
        return 0.0
    cjk = sum(1 for c in text if _is_cjk(c))
    total = sum(1 for c in text if not c.isspace())
    return cjk / total if total > 0 else 0.0


def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF      # CJK Unified
        or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
        or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
        or 0xF900 <= cp <= 0xFAFF    # CJK Compatibility
    )


def classify_document(
    pages_info: list[dict],
    thresholds: dict,
) -> DocClassification:
    """对文档进行构成分类和语种分类。

    抽样：首 sample_pages[0] + 中间 sample_pages[1] + 尾 sample_pages[2] 页。
    """
    sample_cfg = thresholds.get("sample_pages", [5, 2, 2])
    n = len(pages_info)

    indices: set[int] = set()
    # 首 N
    for i in range(min(sample_cfg[0], n)):
        indices.add(i)
    # 尾 N
    for i in range(max(0, n - sample_cfg[2]), n):
        indices.add(i)
    # 中间 N
    if n > sample_cfg[0] + sample_cfg[2]:
        mid = n // 2
        half = sample_cfg[1] // 2
        for i in range(max(sample_cfg[0], mid - half), min(n - sample_cfg[2], mid + half)):
            indices.add(i)

    image_pages = 0
    text_pages = 0
    total_cjk = 0.0
    total_chars = 0
    sampled_count = 0

    for idx in sorted(indices):
        info = pages_info[idx]
        sampled_count += 1
        if info.get("is_image_page", False):
            image_pages += 1
        else:
            text_pages += 1

        page_text = info.get("text", "")
        total_cjk += sum(1 for c in page_text if _is_cjk(c))
        total_chars += sum(1 for c in page_text if not c.isspace())

    sampled_count = max(sampled_count, 1)
    doc_type_ratio = thresholds.get("doc_type_ratio", 0.90)

    if image_pages / sampled_count > doc_type_ratio:
        composition = "image"
    elif text_pages / sampled_count > doc_type_ratio:
        composition = "text"
    else:
        composition = "mixed"

    cjk_ratio = total_cjk / total_chars if total_chars > 0 else 0.0
    if cjk_ratio > 0.90:
        language = "A"
    elif cjk_ratio < 0.10:
        language = "B"
    else:
        language = "C"

    return DocClassification(composition=composition, language=language)


def classify_pages(doc, thresholds: dict) -> list[PageAction]:
    """对全部页进行逐页 action 判定。

    规则（C2 §1）：
    - 文本页（字符>50 且图像面积<50%）→ extract
    - 纯图/图片页 → ocr
    - 混排页图像>=70% → ocr
    - 混排页图像<70% → extract + keep_asset
    - text render mode 3（不可见文本层）→ 视为文本页
    - 图像面积<1% → 不计为图
    """
    import fitz

    page_text_min = thresholds.get("page_text_min_chars", 50)
    image_area_ratio = thresholds.get("image_area_ratio", 0.70)
    results: list[PageAction] = []

    for i in range(len(doc)):
        page = doc[i]
        page_num = i + 1
        text = page.get_text("text")
        char_count = len(text.replace("\n", "").replace(" ", ""))

        # 检测 text render mode 3
        blocks = page.get_text("dict")["blocks"]
        has_invisible_text = any(
            b.get("type") == 0
            and any(
                s.get("render_mode") == 3
                for line in b.get("lines", [])
                for s in line.get("spans", [])
            )
            for b in blocks
        )

        # 图像分析
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        image_entries: list[dict] = []
        total_image_area = 0.0

        for b in blocks:
            if b.get("type") == 1:  # 图像块
                bbox_rect = fitz.Rect(b["bbox"])
                img_area = bbox_rect.width * bbox_rect.height
                ratio = img_area / page_area if page_area > 0 else 0
                if ratio >= 0.01:  # 过滤 <1% 的小图标
                    total_image_area += ratio
                    image_entries.append({
                        "bbox": list(b["bbox"]),
                        "area_ratio": round(ratio, 3),
                        "action": "keep_asset",
                    })

        total_image_ratio = total_image_area

        # 判定（E-2 修订 2026-07-31：有文本层即 extract，不再用字符数硬阈值）
        # 原逻辑 char_count > 50 才 extract，导致 520 页纯文本（每页 33 字符）
        # 与带文本层的模拟扫描件（38 字符）被误判 ocr → 每页 30s PaddleOCR。
        # 修正：只要 PyMuPDF 能抽出文本（char_count > 0）即视为文本页直抽；
        # 无文本层（真扫描件）才走 ocr。
        if has_invisible_text:
            # text render mode 3 → 视为文本页
            results.append(PageAction(page=page_num, action="extract", images=[]))
        elif total_image_ratio >= image_area_ratio:
            # 图像 >=70% → ocr（图占主体，OCR 补文本）
            results.append(PageAction(page=page_num, action="ocr", images=[]))
        elif char_count > 0:
            # 有文本层（哪怕 1 字符）→ extract；有图则附 keep_asset
            results.append(PageAction(page=page_num, action="extract", images=image_entries))
        else:
            # 无文本层（真扫描件）→ ocr
            results.append(PageAction(page=page_num, action="ocr", images=[]))

    return results
