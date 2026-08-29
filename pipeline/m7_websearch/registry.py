"""适配器注册框架（蓝图第十六条：可插拔适配层，T5 A/C 1）。

新增检索源 = 实现 SearchAdapter 接口 + register_adapter()，不改框架代码。
内置适配器经 config.yaml web_search.adapters 列表启用（load_builtin_adapters），
注册动作在适配器自身模块内——框架只维护注册表与扇出合并。

run_search：并行语义暂取串行扇出（源数量少、单源超时可控）；单源异常只记日志
不拖垮整体（归一为空结果，对齐 U4 错误归一思路）。去重合并在 T6 按需加入。
"""

from __future__ import annotations

import logging
from typing import Protocol

from .schema import WebResult

logger = logging.getLogger(__name__)


class SearchAdapter(Protocol):
    """检索源适配器接口。

    query = 检索主词（中文通道下为英译词）；query_orig = 用户原始输入
    （中文通道下千帆学术等中文源用它直搜）；lang = 语言过滤（如 "zh"，
    OpenAlex/DOAJ 归并用；不支持的源忽略）。T7 为中文通道引入后两个
    可选参数，旧适配器（mock）签名保持兼容。
    """

    id: str  # 适配器唯一 id（配置启用/来源筛选用）
    label: str  # 来源标签（结果 source 字段，界面可见）

    def search(
        self,
        query: str,
        max_results: int,
        *,
        query_orig: str | None = None,
        lang: str | None = None,
    ) -> list[WebResult]:
        """执行检索，返回统一 schema 结果；失败抛异常由框架归一。"""
        ...


_ADAPTERS: dict[str, SearchAdapter] = {}
_builtin_loaded = False


def register_adapter(adapter: SearchAdapter) -> None:
    """注册适配器（新增源唯一动作；重复注册同 id 覆盖并记日志）。"""
    if adapter.id in _ADAPTERS:
        logger.info(f"[websearch] 适配器覆盖注册: {adapter.id}")
    _ADAPTERS[adapter.id] = adapter


def list_adapters() -> list[SearchAdapter]:
    """当前已注册适配器（含内置懒加载后的）。"""
    load_builtin_adapters()
    return list(_ADAPTERS.values())


def clear_registry() -> None:
    """清空注册表（测试用；生产代码不得调用）。"""
    global _builtin_loaded
    _ADAPTERS.clear()
    _builtin_loaded = False


def load_builtin_adapters() -> None:
    """按 web_search.adapters 配置懒加载内置适配器（幂等）。

    内置表 _BUILTIN 是框架与适配器的唯一接线点：新适配器模块在此登记
    工厂函数，配置启用即注册——演示路径「新增适配器不改框架代码」指
    适配器自身实现 + 一行登记，不触碰扇出/端点逻辑。
    """
    global _builtin_loaded
    if _builtin_loaded:
        return
    _builtin_loaded = True

    from .adapters.arxiv import ArxivAdapter
    from .adapters.doaj import DoajAdapter
    from .adapters.mock import MockAdapter
    from .adapters.openalex import OpenAlexAdapter
    from .adapters.qianfan import QianfanScholarAdapter

    _BUILTIN = {
        "mock": MockAdapter,
        "qianfan_scholar": QianfanScholarAdapter,
        "openalex": OpenAlexAdapter,
        "doaj": DoajAdapter,
        "arxiv": ArxivAdapter,
    }

    from .config import enabled_builtin_adapters

    for adapter_id in enabled_builtin_adapters():
        factory = _BUILTIN.get(adapter_id)
        if factory is None:
            logger.warning(f"[websearch] 未知内置适配器 id（跳过）: {adapter_id}")
            continue
        register_adapter(factory())


def run_search(
    query: str,
    max_results: int = 8,
    sources: list[str] | None = None,
    *,
    query_orig: str | None = None,
    lang: str | None = None,
) -> list[WebResult]:
    """扇出已注册适配器并合并结果。sources 指定时只跑子集（来源筛选）。

    query_orig/lang 透传给适配器（中文通道：千帆用 query_orig 直搜，
    OpenAlex/DOAJ 用 query + lang 过滤归并）。
    单源异常归一为空结果 + 记日志，不影响其他源（部分可用优于整体失败）。
    """
    query = (query or "").strip()
    if not query:
        return []
    results: list[WebResult] = []
    for adapter in list_adapters():
        if sources and adapter.id not in sources:
            continue
        try:
            results.extend(
                adapter.search(query, max_results, query_orig=query_orig, lang=lang)
            )
        except Exception as e:
            logger.warning(f"[websearch] 适配器 {adapter.id} 检索失败（归一为空）: {e}")
    return results


def _dedup_key(r: WebResult) -> str:
    """去重键：DOI 优先（规范化小写），否则标题（折叠空白 + 小写）。"""
    if r.doi:
        return "doi:" + r.doi.strip().lower()
    return "title:" + " ".join(r.title.split()).lower()


def dedup_results(results: list[WebResult]) -> list[WebResult]:
    """DOI/标题去重合并（蓝图第十六条，T6 A/C 2）。

    同键只留一条：保留先出现者，但后者带 OA 直链而前者没有时换成后者
    （OA 可下载优先，对齐 T8 一键下载仅对 OA 开放的定案）。顺序稳定。
    """
    kept: dict[str, WebResult] = {}
    order: list[str] = []
    for r in results:
        key = _dedup_key(r)
        if key not in kept:
            kept[key] = r
            order.append(key)
        elif r.oa_url and not kept[key].oa_url:
            kept[key] = r
    return [kept[k] for k in order]
