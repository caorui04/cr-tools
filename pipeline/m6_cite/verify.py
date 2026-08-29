"""引用核验门（P3.5-1 A 部分，借鉴 ARS citation gate 方法论）。

目标：引用清单里每条引用，对照文献原文判定"支持/不支持/不确定"，
补全引用链证据闭环（贴合"证据链/不伪造"产品底线）。

设计原则：
- **advisory 非门禁**：核验结果是证据提示，不自动拦截/删除引用。
- **版权红线**：evidence 只回定位信息（节标题/片段短引 ≤120 字）供核对，不做原文浓缩。
- 本地模型判定质量不确定 → verdict 前带"基于本地模型，仅供参考"；建议切云后再核验。
- 判定用 provider._chat_for_json（双试）；provider 不可用 → 启发式降级。
"""

from __future__ import annotations

import logging
import re

from m6_digest.digest_skill import _split_chunks

logger = logging.getLogger(__name__)

VERDICTS = ("supported", "not_supported", "unsupported_unclear")

_VERIFY_SYSTEM = (
    "你是学术引用核验助手。判断给定论点 claim 是否被给定文献原文片段支持。"
    "输出严格 JSON：{\"verdict\": \"supported|not_supported|unsupported_unclear\", "
    "\"evidence\": \"支持或不支持的原文片段（最多120字，必须引用原文）\", "
    "\"confidence\": 0.0-1.0}。"
    "supported = 原文明确支持该论点；not_supported = 原文与论点矛盾或明确不支持；"
    "unsupported_unclear = 片段不足、无关或无法判断。"
    "不要编造证据，找不到就写 empty。"
)


def _locate_relevant(text: str, claim: str, top_n: int = 4) -> list[str]:
    """挑与 claim 相关的原文块（启发式：关键词交集 + 位置优先）。"""
    chunks = _split_chunks(text)
    claim_terms = {
        t for t in re.split(r"\W+", claim.lower()) if len(t) >= 3
    }
    if not claim_terms:
        return chunks[:top_n]
    scored = []
    for idx, c in enumerate(chunks):
        c_low = c.lower()
        hits = sum(1 for t in claim_terms if t in c_low)
        scored.append((hits, idx))
    scored.sort(key=lambda x: (-x[0], x[1]))
    # 无命中 → 返回开头几块（研究背景常含主题词）
    if scored[0][0] == 0:
        return chunks[:top_n]
    return [chunks[i] for _, i in scored[:top_n]]


def _heuristic_verdict(chunks: list[str], claim: str) -> dict:
    """启发式降级：关键词命中率 → supported/unclear，绝不判 not_supported（避免误杀）。"""
    terms = {t for t in re.split(r"\W+", claim.lower()) if len(t) >= 3}
    if not terms:
        return {
            "verdict": "unsupported_unclear",
            "evidence": "empty",
            "confidence": 0.0,
            "mode": "heuristic",
        }
    best = max(
        (sum(1 for t in terms if t in c.lower()) / len(terms) for c in chunks),
        default=0.0,
    )
    if best >= 0.4:
        # 找到部分相关片段：提示性支持，明确标 heuristic
        hit = next((c for c in chunks if sum(1 for t in terms if t in c.lower()) / len(terms) >= 0.4), "")
        return {
            "verdict": "supported",
            "evidence": (hit[:120] + "…") if len(hit) > 120 else hit,
            "confidence": round(0.4 + 0.3 * best, 2),
            "mode": "heuristic",
        }
    return {
        "verdict": "unsupported_unclear",
        "evidence": "empty",
        "confidence": 0.1,
        "mode": "heuristic",
    }


def _llm_verdict(chunks: list[str], claim: str, provider) -> dict | None:
    """LLM 判定（双试）。失败返回 None 交给启发式。"""
    chat = getattr(provider, "_chat_for_json", None)
    if chat is None:
        return None
    user = (
        f"论点 claim：{claim}\n\n"
        f"文献原文片段（按相关度排序，用空行分隔）：\n\n"
        + "\n\n".join(f"--- 片段 {i+1} ---\n{c[:800]}" for i, c in enumerate(chunks))
    )
    for attempt in range(2):
        try:
            raw = chat(_VERIFY_SYSTEM, user)
            verdict = raw.get("verdict")
            if verdict not in VERDICTS:
                continue
            evidence = str(raw.get("evidence", "empty"))[:160]
            if evidence.lower() == "empty" or not evidence.strip():
                evidence = "empty"
            conf = float(raw.get("confidence", 0.5))
            return {
                "verdict": verdict,
                "evidence": evidence,
                "confidence": round(max(0.0, min(1.0, conf)), 2),
                "mode": "llm",
            }
        except Exception as e:
            logger.warning(f"核验第 {attempt + 1} 次失败: {e}")
    return None


def verify_citation(doc_id: str, claim: str, full_text: str, provider=None) -> dict:
    """核验：论点 claim 是否被文献 doc_id 的原文支持。

    Args:
        doc_id: 文献 ID
        claim: 论文中的论点陈述（用户粘贴）
        full_text: 文献原文全文（路由层已定位，本函数不碰文件系统）
        provider: 当前 LLMProvider（None 则纯启发式）

    Returns:
        {"doc_id", "claim", "verdict", "evidence", "confidence", "mode", "warning"}
    """
    if not claim or not claim.strip():
        return {
            "doc_id": doc_id, "claim": "", "verdict": "unsupported_unclear",
            "evidence": "empty", "confidence": 0.0, "mode": "none",
            "warning": "缺少论点 claim，无法核验。",
        }
    text = (full_text or "").strip()
    if len(text) < 200:
        return {
            "doc_id": doc_id, "claim": claim, "verdict": "unsupported_unclear",
            "evidence": "empty", "confidence": 0.0, "mode": "none",
            "warning": "文献文本过短或未入库，无法核验。",
        }
    chunks = _locate_relevant(text, claim)
    result = _llm_verdict(chunks, claim, provider)
    if result is None:
        result = _heuristic_verdict(chunks, claim)
        result["warning"] = "本地模型不可用，当前为关键词启发式核验，仅供参考。"
    else:
        result["warning"] = "核验基于本地模型，仅供参考；重要引用请回原文核对。"
    result["doc_id"] = doc_id
    result["claim"] = claim
    return result
