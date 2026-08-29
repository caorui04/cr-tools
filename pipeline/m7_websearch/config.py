"""web_search 节配置加载 + 联网检索可用态判定（T5 A/C 4/A/C 5）。

- web_search_config()：读权威 config.yaml 的 web_search 节（千帆/OpenAlex Key 等）。
  权威路径由包位置推导（对齐 http_client._CONFIG_PATH，禁止 Path.cwd()）；
  本文件含明文 Key，分发构建时整节剥离——读取失败/节缺失一律按空 dict 处理。
- web_search_available()：完全本地模式判定 = analysis.web_search_enabled=false
  或当前分析后端 = off（第十八条：完全本地模式下联网检索整个不可用）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 权威 config.yaml：pipeline 包根目录（本文件位于 <root>/m7_websearch/config.py）。
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def web_search_config() -> dict:
    """读 config.yaml 的 web_search 节（Key 加载唯一入口，A/C 4）。异常按空节处理。"""
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return raw.get("web_search") or {}
    except Exception as e:
        logger.warning(f"[websearch] web_search 节读取失败，按空配置处理: {e}")
        return {}


def enabled_builtin_adapters() -> list[str]:
    """web_search.adapters 列表：启用的内置适配器 id（如 ["mock"]；缺省空 = 无源）。"""
    adapters = web_search_config().get("adapters") or []
    return [str(a) for a in adapters]


def web_search_available() -> bool:
    """完全本地模式判定（A/C 5）：开关关闭或分析后端 off 即整体不可用。"""
    from pipeline_core.http_client import web_search_enabled

    if not web_search_enabled():
        return False
    try:
        from llm.providers import get_analysis_backend

        return get_analysis_backend() != "off"
    except Exception:
        # providers 未装配（如纯测试上下文）：不因此误判不可用
        return True
