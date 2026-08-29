"""M3 翻译 — 块计划生成 + LLM 翻译 + 双语 MD 输出。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Block:
    seq: int
    type: str            # "code" | "formula" | "text"
    action: str          # "keep" | "translate"
    text: str
    lang_foreign_ratio: float = 0.0
    char_count: int = 0
    translated: str = ""  # 翻译结果（translate 块）


def count_foreign_ratio(text: str) -> float:
    """外语字符占比。非 CJK/标点/空白 → 外语。"""
    if not text:
        return 0.0
    foreign = 0
    total = 0
    for c in text:
        if c.isspace():
            continue
        total += 1
        cp = ord(c)
        if not (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F):
            foreign += 1
    return foreign / total if total > 0 else 0.0


def parse_md_blocks(md_text: str, thresholds: dict) -> list[Block]:
    """按 MD 结构切分块并判定 action。"""
    lang_ratio = thresholds.get("lang_foreign_ratio", 0.80)
    block_min = thresholds.get("block_min_chars", 50)

    lines = md_text.split("\n")
    blocks: list[Block] = []
    seq = 0
    in_code = False
    in_formula = False
    buffer: list[str] = []

    for line in lines:
        # 代码围栏
        if line.strip().startswith("```"):
            if buffer:
                blocks.append(_make_block(seq, buffer, lang_ratio, block_min))
                seq += 1
                buffer = []
            in_code = not in_code
            buffer.append(line)
            if not in_code:
                blocks.append(Block(seq=seq, type="code", action="keep", text="\n".join(buffer)))
                seq += 1
                buffer = []
            continue

        if in_code:
            buffer.append(line)
            continue

        # 公式（单行完整 $$...$$）
        if line.strip().startswith("$$") and line.strip().endswith("$$") and len(line.strip()) > 4:
            if buffer:
                blocks.append(_make_block(seq, buffer, lang_ratio, block_min))
                seq += 1
                buffer = []
            blocks.append(Block(seq=seq, type="formula", action="keep", text=line))
            seq += 1
            continue

        # 公式块开始/结束
        if line.strip().startswith("$$"):
            if buffer:
                blocks.append(_make_block(seq, buffer, lang_ratio, block_min))
                seq += 1
                buffer = []
            in_formula = not in_formula
            buffer.append(line)
            if not in_formula:
                blocks.append(Block(seq=seq, type="formula", action="keep", text="\n".join(buffer)))
                seq += 1
                buffer = []
            continue

        if in_formula:
            buffer.append(line)
            continue

        # 空行 → 分段
        if line.strip() == "" and buffer:
            blocks.append(_make_block(seq, buffer, lang_ratio, block_min))
            seq += 1
            buffer = []
            continue

        buffer.append(line)

    if buffer:
        blocks.append(_make_block(seq, buffer, lang_ratio, block_min))

    return blocks


def _make_block(seq: int, lines: list[str], lang_ratio: float, block_min: int) -> Block:
    text = "\n".join(lines)
    fr = count_foreign_ratio(text)
    cc = sum(1 for c in text if not c.isspace())
    if fr >= lang_ratio and cc >= block_min:
        return Block(seq=seq, type="text", action="translate", text=text, lang_foreign_ratio=fr, char_count=cc)
    return Block(seq=seq, type="text", action="keep", text=text, lang_foreign_ratio=fr, char_count=cc)


def build_bilingual_md(blocks: list[Block]) -> str:
    """拼接双语 MD。translate 块→原文+译文；keep 块→原样。"""
    parts = []
    for b in blocks:
        if b.action == "translate":
            parts.append(b.text)
            if b.translated:
                parts.append("")
                parts.append(b.translated)
        else:
            parts.append(b.text)
        parts.append("")
    return "\n".join(parts)
