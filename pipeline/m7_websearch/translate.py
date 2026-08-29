"""关键词英译降级链（蓝图第十五/十八条定案，T5 A/C 2/A/C 3）。

链路：检测到中文 → ① 云端大模型（第十八条：翻译直接调云端，轻任务 1-2s，
独立于当前分析后端开关——联网检索本身即联网动作，无新增暴露面）→
② 本地模型（仅已加载则用：check_alive 探测 llama.cpp，绝不主动拉起）→
③ 不翻译直接提交 + 界面提示「翻译不可用，建议输入英文关键词」。

英文/纯拉丁输入不翻译（method=none，translated=原文，hint 空）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 降级到底时的界面提示（A/C 3 定案文案）
HINT_NO_TRANSLATE = "翻译不可用，建议输入英文关键词"


def contains_cjk(text: str) -> bool:
    """是否含 CJK 表意文字（中/日/韩汉字；假名/韩音节不在内，学术关键词场景够用）。"""
    return any("一" <= ch <= "鿿" for ch in text)  # U+4E00–U+9FFF CJK 统一表意文字


# 会话内翻译缓存（Q4：本地检索双 query 复用同一翻译链，避免同词重复调云端）
_CACHE: dict[str, dict] = {}


def translate_query(query: str) -> dict:
    """带会话缓存的入口：同词直接命中缓存（Q4 本地检索每次保存/检索都会调）。"""
    key = (query or "").strip()
    if key not in _CACHE:
        _CACHE[key] = _translate_uncached(key)
    return _CACHE[key]


def _translate_uncached(query: str) -> dict:
    """关键词英译。返回 {original, translated, method, hint}。

    method: "cloud" | "local" | "none"（none = 未翻译，拉丁原文直交或全链失败）。
    全链失败时 translated=原文、hint=HINT_NO_TRANSLATE（调用方直接提交并提示）。
    """
    original = (query or "").strip()
    if not contains_cjk(original):
        return {"original": original, "translated": original, "method": "none", "hint": ""}

    # ① 云端（独立直构，不依赖分析后端开关；Key 缺失/超时/异常一律降级）
    try:
        from llm.factory import get_provider
        from pipeline_core.config import load_config

        llm_cfg = load_config().models.llm
        cloud = llm_cfg.cloud
        provider = get_provider(
            {
                "prefer": "cloud",
                "allow_cloud_fallback": False,
                "cloud": {
                    "base_url": cloud.base_url,
                    "api_key": cloud.api_key,
                    "api_key_env": cloud.api_key_env,
                    "model": cloud.model,
                    "api_style": getattr(cloud, "api_style", "chat_completions"),
                },
            }
        )
        if getattr(provider, "name", "") != "stub":
            translated = (provider.translate(original, "zh", "en") or "").strip()
            if translated:
                logger.info(f"[websearch] 关键词英译（云端）: {original!r} → {translated!r}")
                return {"original": original, "translated": translated, "method": "cloud", "hint": ""}
    except Exception as e:
        logger.warning(f"[websearch] 云端英译失败，尝试本地: {e}")

    # ② 本地已加载模型（check_alive 只探测不拉起；第十八条「已加载则用」）
    try:
        from llm.llama_chat_provider import LlamaCppChatProvider
        from pipeline_core.config import load_config

        llm_cfg = load_config().models.llm
        lp = getattr(llm_cfg, "llama_cpp", None)
        port = getattr(lp, "port", 8082)
        model_name = getattr(lp, "model_name", "local-model")
        provider = LlamaCppChatProvider(
            base_url=f"http://127.0.0.1:{port}", model_name=model_name
        )
        if provider.check_alive():
            translated = (provider.translate(original, "zh", "en") or "").strip()
            if translated:
                logger.info(f"[websearch] 关键词英译（本地 {model_name}）: {original!r} → {translated!r}")
                return {"original": original, "translated": translated, "method": "local", "hint": ""}
        else:
            logger.info("[websearch] 本地模型未加载（不主动拉起），跳过本地英译")
    except Exception as e:
        logger.warning(f"[websearch] 本地英译失败: {e}")

    # ③ 不翻译直接提交 + 提示
    logger.info(f"[websearch] 英译全链失败，原文直交: {original!r}")
    return {
        "original": original,
        "translated": original,
        "method": "none",
        "hint": HINT_NO_TRANSLATE,
    }
