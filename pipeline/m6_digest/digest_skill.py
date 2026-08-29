"""M6-digest 速览卡（P3.5-1 第 3 步）。

从已入库文档全文生成"思想层"速览卡（核心论点/章节地图/关键概念），
不摘录原文、不做浓缩摘要（版权红线：思想层不侵权，原文层不做）。

provider 约定（duck-typing，不改 P4L 契约源码）：
- 有 extract_digest(text, format) 方法 → 用之（协议扩展点）
- 有 _chat_for_json(system, user) → 分段 LLM 提炼（llama_cpp/cloud 都有）
- 都没有（stub）→ 结构化启发式降级（标题/分节/首句）

本地 3B 质量不达标属预期：启发式兜底保证有输出，P3.5-2 再评。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_provider = None

CHUNK_CHARS = 800    # 分段长度（本地 3B 对 1200 字长块提炼会复述指令/JSON 解析失败；800 实测通过率最高）
MAX_CHUNKS = None    # 全篇提炼（不设上限；长文生成时间线性增长，由路由层提供进度轮询）

_SYSTEM = (
    "你是一名学术论文阅读助手。请提炼给定文本的思想要点。"
    "**铁律：只能提炼原文中明确出现的内容，禁止添加原文没有的信息，"
    "禁止用你的常识、联想或行业套话补充。若原文没有可提炼的实质内容，"
    "heading 填 \"empty\"、points 填 []。**"
    "输出严格 JSON：{\"heading\": \"本段主题（6-20字）\", \"points\": [\"要点1\", \"要点2\"]}，"
    "每个要点 15-40 字，只提炼论点与结构，不要摘录原文句子。"
)


def _extract_cn_terms(text: str) -> list[str]:
    """提取文本中 ≥2 字的连续汉字片段（中文关键词）。

    长 heading/要点整条连续出现在原文的概率低（原文是散文，词分散），
    故对每个片段额外生成 3-4 字滑动窗口词——窗口词只要真实出现在原文
    即可命中溯源，避免误杀。窗口词为非词组合（如"国高"）误报风险低：
    溯源要求窗口词确实出现在原文。
    """
    out = []
    for m in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(m) >= 2:
            out.append(m)
        for w in range(3, 5):
            for i in range(max(0, len(m) - w + 1)):
                out.append(m[i : i + w])
    return out


def _grounded_in(terms: list[str], chunk: str) -> bool:
    """要点是否能在对应原文块中找到依据（关键词包含性校验，防幻觉）。

    要求至少一个 ≥2 字中文关键词在该 chunk 中出现。粗校验挡明显幻觉
    （如"机器学习在医疗诊断中的应用"——关键词全不在原文）；模型同义
    改写导致的漏检宁可丢弃（保守优先：速览卡宁缺毋假，踩"不伪造"底线
    比不完整更严重）。
    """
    if not terms:
        return False
    c_low = chunk.lower()
    return any(t in c_low for t in terms)


_NOISE_PATTERNS = [
    r"<!--.*?-->",           # HTML/页码注释
    r"\([^)]*\.\s*\)$",      # 文末括号机构/单位：(山东师范大学图书馆，济南 250358)
    r"[（(][^）)]*250\d{3}[^）)]*[）)]",  # 邮编
    r"^\(?\d+[.)、]\s*$",     # 纯编号行
    r"XX",                     # 占位符
    r"第.{1,4}期|第.{1,4}卷",  # 期刊期卷号
    r"^[A-Z][a-z]+$",          # 英文单词残留（OCR）
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?[,，]?\s*\d{4}",  # 期刊日期页眉：Dec.,2024
    r"^Vol\.?\s*\d+",          # 期刊卷页眉：Vol. 45
    r"^No\.?\s*\d+",           # 期刊期页眉：No. 12
]


def _is_noise(text: str) -> bool:
    """判定是否为非内容行（机构/期刊信息/占位符/编号/OCR 残留）。"""
    import re as _re
    if len(text) < 6:
        return True
    return any(_re.search(p, text) for p in _NOISE_PATTERNS)


def set_provider(p):
    """注入 LLMProvider 实例（providers.py 调用）。"""
    global _provider
    _provider = p


def _split_chunks(text: str, size: int = CHUNK_CHARS) -> list[dict]:
    """切段（保留换行边界，尽量填满 size），返回 [{text, pages:[首,末]}].

    切分前先滤除无内容行（页码注释/期刊页眉/极短残留等），再按行聚合填块：
    密集页眉（逐行切会产出大量碎片小块）会把 MAX_CHUNKS 预算占满、
    正文被截断——见《鲁迅与抗战》样例：12 块全是页眉/基金/作者简介。
    聚合填块后 12 × 800 字覆盖量最大化，正文提前进入提炼。

    pages 为物理页码（1-based），来自原文 md 的 <!-- page N --> 标注，
    供速览卡"章节地图 → 预览跳页"锚点用。
    """
    content = []  # (line, page)
    current_page = 1
    for raw in text.split("\n"):
        line = raw.strip()
        m = re.match(r"^<!--\s*page\s*(\d+)\s*-->", line)
        if m:
            current_page = int(m.group(1))
            continue
        if not line:
            continue
        # OCR 页眉纯数字页码行（与 <!-- page N --> 同义，跳过不计入内容）
        if re.fullmatch(r"\d{1,4}", line):
            continue
        # 期刊页眉/元数据噪声行（刊名/卷期/日期/基金/作者简介等）
        if _is_noise(line):
            continue
        content.append((line, current_page))
    chunks = []
    buf = ""
    buf_pages = []
    for line, pg in content:
        if buf and len(buf) + len(line) + 1 > size:
            chunks.append({"text": buf, "pages": [min(buf_pages), max(buf_pages)]})
            buf = line
            buf_pages = [pg]
        else:
            buf = buf + "\n" + line if buf else line
            buf_pages.append(pg)
    if buf:
        chunks.append({"text": buf, "pages": [min(buf_pages), max(buf_pages)]})
    return chunks if MAX_CHUNKS is None else chunks[:MAX_CHUNKS]


def _llm_digest(provider, chunks: list[dict], full_text: str, on_progress=None) -> dict | None:
    """分段 LLM 提炼，合并为 {sections: [{heading, points, pages}]}。任何失败降级 None。

    chunks: [{text, pages:[首,末]}]（_split_chunks 输出）；每块提炼一个 section，
    附该块的物理页码区间（速览卡"章节地图 → 预览跳页"锚点）。
    on_progress(done, total)：每块开始前回调一次，供路由层进度轮询。

    防幻觉：每条要点溯源校验——关键词须在整篇原文出现（非仅本 chunk，
    3B 提炼常跨块同义改写；整篇校验仍能挡"机器学习在医疗诊断"这类整体幻觉）。
    """
    if provider is None:
        return None
    if hasattr(provider, "extract_digest"):
        try:
            raw = provider.extract_digest("\n\n".join(c["text"] for c in chunks), "digest")
            if isinstance(raw, dict) and raw.get("sections"):
                return raw
        except Exception as e:
            logger.warning(f"extract_digest 失败，降级: {e}")

    chat = getattr(provider, "_chat_for_json", None)
    if chat is None:
        return None

    sections = []
    total = len(chunks)
    for idx, chunk_info in enumerate(chunks):
        if on_progress:
            on_progress(idx + 1, total)
        chunk = chunk_info["text"]
        try:
            raw = chat(_SYSTEM, chunk)
            if not (isinstance(raw, dict) and raw.get("heading")):
                continue
            heading = str(raw.get("heading", ""))[:60]
            if heading.lower() in ("empty", "") or _is_noise(heading) or not _grounded_in(_extract_cn_terms(heading), full_text):
                continue
            points = []
            for p in (raw.get("points") or [])[:5]:
                ps = str(p)[:120]
                if ps and not _is_noise(ps) and _grounded_in(_extract_cn_terms(ps), full_text):
                    points.append(ps)
            if not points:
                continue
            section = {"heading": heading, "points": points}
            if chunk_info.get("pages"):
                section["pages"] = chunk_info["pages"]
            sections.append(section)
        except Exception as e:
            logger.debug(f"分段提炼失败（跳过该段）: {e}")
    return {"sections": sections} if sections else None


def _extract_title(lines: list[str]) -> str:
    """提取标题：优先 Markdown # 标题行；否则取 8-60 字的实质行（跳过噪声/注码/脚注）。"""
    for l in lines:
        s = l.strip()
        if s.startswith("# "):
            return s.lstrip("# ").strip()[:80]
    for l in lines[:40]:
        s = l.strip()
        if not s or s.startswith(("#", "-", "*", "|", ">", "<!--")):
            continue
        if _is_noise(s):
            continue
        # 排除含注码/特殊字符（ª º ‹ › 等脚注符号）或过短行的非标题
        import re as _re
        if _re.search(r"[ªº‹›⟨⟩\[\]①②③④⑤⑥⑦⑧⑨⑩]", s):
            continue
        if 8 <= len(s) <= 60:
            return s[:80]
    return ""


def _heuristic_card(text: str) -> dict:
    """启发式降级：标题（首行）+ 分节标题（# 行）+ 各节首句要点（全部经噪声过滤）。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    title = _extract_title(lines)
    sections = []
    current = {"heading": "开头", "points": []}
    for l in lines:
        if re.match(r"^#{1,6}\s+", l):
            if current["points"]:
                sections.append(current)
            current = {"heading": re.sub(r"^#{1,6}\s+", "", l)[:60], "points": []}
        elif l.startswith(("1.", "2.", "一、", "二、", "（", "(")):
            if not _is_noise(l):
                current["points"].append(l[:100])
    if current["points"]:
        sections.append(current)
    if not sections:
        sections = [{"heading": "全文", "points": [l[:100] for l in lines[:6] if not _is_noise(l)]}]
    return {
        "title": title or "（未识别标题）",
        "sections": sections,
    }


def _build_card(llm_card: dict) -> dict:
    """把 LLM 提炼结果组织为速览卡结构。"""
    sections = llm_card.get("sections", [])
    core_points = []
    for s in sections[:4]:
        for p in s.get("points", [])[:2]:
            if p and p not in core_points:
                core_points.append(p)
    # 关键概念：各节首个要点（去重、去超长）
    concepts = []
    for s in sections:
        for p in s.get("points", []):
            if 2 <= len(p) <= 30 and p not in concepts:
                concepts.append(p)
            if len(concepts) >= 8:
                break
        if len(concepts) >= 8:
            break
    return {
        "core_points": core_points[:8],
        "sections": sections,
        "key_concepts": concepts,
        "relevance": "结合点建议：后续版本基于论文草稿提供（需 kind=paper 项目）。",
    }


def make_digest(full_text: str, provider=None, on_progress=None) -> dict:
    """生成速览卡。provider 未注入/不可用时走启发式降级（保证有输出）。

    on_progress(done, total)：LLM 分段提炼期间逐块回调（供进度轮询）。
    """
    text = (full_text or "").strip()
    if len(text) < 200:
        return {
            "title": "（文本过短）",
            "sections": [],
            "core_points": ["文档文本过短，无法提炼。"],
            "key_concepts": [],
            "relevance": "",
        }
    chunks = _split_chunks(text)
    llm_card = _llm_digest(provider, chunks, text, on_progress)
    if llm_card and llm_card.get("sections"):
        return _build_card(llm_card)
    logger.info("LLM 提炼不可用/质量不足，使用启发式速览卡")
    return _heuristic_card(text)
