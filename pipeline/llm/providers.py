"""分析后端可插拔（P3.5-1 第 4 步）。

应用级 provider 获取 + cite/digest 注入。三种模式：

- local：默认。走 get_provider() 降级链（llama_cpp → cloud → stub）。
- cloud：用户自填云端（config.yaml models.llm.cloud，OpenAI 兼容）。
- off：完全本地模式。只允许 llama_cpp/gguf；不可用时 cite/digest 走 stub，绝不触网。

接线模式照 P7 先例（m6 自动补录的 set_provider 注入；该模块已于 R3 清理移除）：set_provider() 注入模块级变量。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 当前模式与 provider 缓存
_analysis_backend = "local"
_provider = None          # 缓存当前 LLMProvider（None = 未注入/不可用）
_provider_error: str | None = None


def _build_factory_config(mode: str) -> dict:
    """按模式从 config.yaml 构造 factory 参数 dict。"""
    from pipeline_core.config import load_config

    cfg = load_config()
    llm_cfg = cfg.models.llm

    # 构造 llm 子 dict（factory.get_provider 兼容：有 models 键当完整 config 处理）
    d = {
        "prefer": llm_cfg.prefer,
        "allow_cloud_fallback": False if mode == "off" else llm_cfg.allow_cloud_fallback,
    }
    # llama_cpp 段（本地聊天后端；生产版 LLMConfig 有该属性，契约版无 → 兼容回退）
    llama_cpp = getattr(llm_cfg, "llama_cpp", None)
    if llama_cpp is not None:
        d["llama_cpp"] = {
            "port": getattr(llama_cpp, "port", 8082),
            "model_name": getattr(llama_cpp, "model_name", "qwen2.5-3b"),
        }
    elif llm_cfg.prefer == "llama_cpp":
        # 契约版（pack-P4L）无 llama_cpp 属性：回退默认端口/模型
        d["llama_cpp"] = {"port": 8082, "model_name": "qwen2.5-3b"}
    elif llm_cfg.prefer == "gguf":
        d["gguf"] = {
            "port": getattr(llm_cfg.gguf, "port", 8085),
            "server": getattr(llm_cfg.gguf, "server", "llama-server"),
            "model_path": getattr(llm_cfg.gguf, "model_path", ""),
        }
    # cloud 段（仅在允许云端时附带；off 模式不读 cloud，保证不触网）
    if mode == "cloud":
        cloud = llm_cfg.cloud
        d["cloud"] = {
            "base_url": cloud.base_url,
            "api_key": cloud.api_key,
            "api_key_env": cloud.api_key_env,
            "model": cloud.model,
        }
        # cloud 模式以 cloud 为首选
        d["prefer"] = "cloud"
        d["allow_cloud_fallback"] = False
    return d


def _inject(provider) -> None:
    """把 provider 注入 m6_cite 与 m6_digest。"""
    global _provider
    _provider = provider
    try:
        from m6_cite.cite_skill import set_provider as set_cite_provider
        set_cite_provider(provider)
    except ImportError as e:  # 管线未组装完时静默
        logger.debug(f"cite provider 注入失败（忽略）: {e}")
    try:
        from m6_digest.digest_skill import set_provider as set_digest_provider
        set_digest_provider(provider)
    except ImportError as e:
        logger.debug(f"digest provider 注入失败（忽略）: {e}")


def get_analysis_backend() -> str:
    return _analysis_backend


def ensure_analysis() -> None:
    """启动/每次请求时保证注入一次（幂等）。"""
    if _provider is not None:
        return
    _refresh("local")


def _refresh(mode: str) -> None:
    """按模式重建 provider 并注入；不可用时缓存错误消息。"""
    global _analysis_backend, _provider_error
    _analysis_backend = mode
    _provider_error = None

    from llm.factory import get_provider

    factory_cfg = _build_factory_config(mode)

    # off 模式：完全本地，只允许 llama_cpp/gguf；cloud 段已剔除，天然不触网
    provider = get_provider(factory_cfg)
    _inject(provider)
    if getattr(provider, "name", "") == "stub":
        _provider_error = (
            "本地模型不可用（llama.cpp 未运行或未安装），当前为降级 stub 模式。"
            "请在『环境配置向导』中完成模型安装与启动。"
        )
        logger.warning(_provider_error)


def set_analysis_backend(mode: str) -> dict:
    """切换分析后端。

    Args:
        mode: "local" | "cloud" | "off"

    Returns:
        {"success": bool, "mode": str, "message": str, "provider": str}
    """
    if mode not in ("local", "cloud", "off"):
        return {"success": False, "mode": _analysis_backend, "message": f"未知后端模式: {mode}", "provider": _provider.name if _provider else ""}

    if mode == "cloud":
        from pipeline_core.config import load_config
        cloud = load_config().models.llm.cloud
        if not (getattr(cloud, "api_key", "") or ""):
            return {
                "success": False,
                "mode": _analysis_backend,
                "message": "云端后端需要 API key：请在 config.yaml models.llm.cloud 配置，或设置环境变量 DEEPSEEK_API_KEY",
                "provider": _provider.name if _provider else "",
            }

    _refresh(mode)
    return {
        "success": _provider_error is None,
        "mode": _analysis_backend,
        "message": _provider_error or f"分析后端已切换至 {mode}（{getattr(_provider, 'name', 'stub')}）",
        "provider": getattr(_provider, "name", ""),
    }


def active_provider():
    """获取当前注入的 provider（供 cite/digest 路由使用）。"""
    ensure_analysis()
    return _provider
