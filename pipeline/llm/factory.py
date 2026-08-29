"""get_provider() 工厂函数 + 降级链。

降级链：gguf → cloud → stub（config 可关 cloud）。
当前实际使用 provider 写入 sidecar engines.translator。
"""

from __future__ import annotations

import logging
from typing import Any

from .cloud_provider import CloudApiProvider
from .gguf_provider import GgufProvider
from .llama_chat_provider import LlamaCppChatProvider
from .provider import LLMProvider
from .stub_provider import StubProvider

logger = logging.getLogger(__name__)


def get_provider(config: dict[str, Any]) -> LLMProvider:
    """按 config.models.llm 配置实例化 provider。

    降级链：gguf → cloud → stub。
    不可用时自动按降级链回退。

    Args:
        config: 完整 PipelineConfig dict 或 models.llm 子 dict。

    Returns:
        LLMProvider 实例。
    """
    # 支持传入完整 config 或 llm 子 dict
    llm_cfg = config.get("models", {}).get("llm", config) if "models" in config else config

    prefer = llm_cfg.get("prefer", "stub")
    allow_cloud = llm_cfg.get("allow_cloud_fallback", True)

    # 1) 尝试首选
    if prefer == "llama_cpp":
        provider = _try_llama_chat(llm_cfg)
        if provider is not None:
            logger.info(f"LLM provider: {provider.name}")
            return provider
        logger.warning("llama_cpp 不可用，尝试降级")

        if allow_cloud:
            provider = _try_cloud(llm_cfg)
            if provider is not None:
                logger.info(f"LLM provider（降级至 cloud）: {provider.name}")
                return provider
            logger.warning("Cloud 不可用，降级至 stub")

    elif prefer == "gguf":
        provider = _try_gguf(llm_cfg)
        if provider is not None:
            logger.info(f"LLM provider: {provider.name}")
            return provider
        logger.warning("GGUF 不可用，尝试降级")

        if allow_cloud:
            provider = _try_cloud(llm_cfg)
            if provider is not None:
                logger.info(f"LLM provider（降级至 cloud）: {provider.name}")
                return provider
            logger.warning("Cloud 不可用，降级至 stub")

    elif prefer == "cloud":
        provider = _try_cloud(llm_cfg)
        if provider is not None:
            logger.info(f"LLM provider: {provider.name}")
            return provider
        logger.warning("Cloud 不可用，降级至 stub")

    # 2) 最终降级至 stub
    logger.info("LLM provider: stub")
    return StubProvider()


def _try_gguf(cfg: dict) -> GgufProvider | None:
    """尝试实例化 GgufProvider 并探测存活。"""
    gguf_cfg = cfg.get("gguf", {})
    port = gguf_cfg.get("port", 8085)
    server = gguf_cfg.get("server", "llama-server")

    # 从 model_path 提取模型名
    model_path = gguf_cfg.get("model_path", "")
    model_name = model_path.split("/")[-1].split("\\")[-1] if model_path else "unknown"

    provider = GgufProvider(
        model_name=model_name,
        server=server,
        port=port,
        ctx_size=gguf_cfg.get("ctx_size", 8192),
    )

    if provider.check_alive():
        return provider
    return None


def _try_cloud(cfg: dict) -> CloudApiProvider | None:
    """尝试实例化 CloudApiProvider。"""
    cloud_cfg = cfg.get("cloud", {})
    api_key_env = cloud_cfg.get("api_key_env", "DEEPSEEK_API_KEY")
    api_key = cloud_cfg.get("api_key", "")

    import os
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env, "")

    if not api_key:
        logger.warning(f"Cloud API key 未设置（env: {api_key_env}），跳过")
        return None

    return CloudApiProvider(
        base_url=cloud_cfg.get("base_url", "https://api.deepseek.com"),
        api_key=api_key,
        api_key_env=api_key_env,
        model=cloud_cfg.get("model", "deepseek-chat"),
        api_style=cloud_cfg.get("api_style", "chat_completions"),
    )


def _try_llama_chat(cfg: dict) -> LlamaCppChatProvider | None:
    """尝试实例化 LlamaCppChatProvider 并探测存活。"""
    llm_cfg = cfg.get("models", {}).get("llm", cfg) if "models" in cfg else cfg
    lp_cfg = llm_cfg.get("llama_cpp", {})
    port = lp_cfg.get("port", 8082)
    model_name = lp_cfg.get("model_name", "local-model")

    provider = LlamaCppChatProvider(
        base_url=f"http://127.0.0.1:{port}",
        model_name=model_name,
    )

    if provider.check_alive():
        return provider
    return None
