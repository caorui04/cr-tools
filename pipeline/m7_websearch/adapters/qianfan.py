"""千帆·百度学术适配器（T7 中文文献主通道）。

主通道 A2c 智能搜索生成（蓝图第十四条 2026-08-11 定案采纳）：
POST /v2/ai_search/chat/completions，baidu_search_v2 + search_filter.match.site
定向 xueshu.baidu.com + instruction 提示词工程产结构化 JSON（字段铁律：无信息
填 null 禁止猜测）+ ref_id 关联 references 详情页 URL；模型 ernie-4.5-turbo-32k。

降级通道 A2b 纯搜索（模型服务不可用/鉴权失败/输出解析失败时自动回退）：
POST /v2/ai_search/web_search，同 site 定向，返回网页片段（≤2000 字）——
结果带 note 降级标记（界面提示，T7 A/C 2）。

Key 从 config.yaml web_search.qianfan.api_key 加载（T5 A/C 4）；出站走
U4 http_client.post_json（redact Key）。已知局限（核实清单 A2c 行）：期刊名/
年份常缺、无 DOI、偶发非学术条目、泛 query 可能返回空。
"""

from __future__ import annotations

import json
import logging
import re

from pipeline_core.http_client import post_json

from ..config import web_search_config
from ..schema import WebResult

logger = logging.getLogger(__name__)

_BASE = "https://qianfan.baidubce.com/v2/ai_search"
_MODEL = "ernie-4.5-turbo-32k"  # 2026-08-11 实测可用（deepseek-v3.1 未开通）
_SITE = ["xueshu.baidu.com"]
_TIMEOUT_A2C = 60  # 智能搜索生成含大模型推理（实测单次 3-5k tokens），给足
_TIMEOUT_A2B = 20

# 降级标记（A/C 2 界面提示）
NOTE_DEGRADED = "降级：智能搜索生成不可用，已回退网页片段结果（字段可能不全，建议点详情页核对）"

# A2c instruction（≤4000 字符；字段铁律 + ref_id 关联为实测定案口径）
_INSTRUCTION = """你是中文学术文献检索助手。基于本次搜索结果（均来自百度学术），提取学术文献条目，输出严格 JSON 数组，不要输出任何其他文字、解释或 markdown 围栏。
每个元素字段：
- title：文献标题（字符串）
- authors：作者列表（字符串数组，无确切信息填 []）
- journal：期刊名（字符串，无确切信息填 null）
- year：发表年份（4 位数字字符串，无确切信息填 null）
- abstract：摘要（依据搜索片段整理，不超过 200 字，无确切信息填 null）
- ref_id：对应该文献的搜索结果编号（整数，必须取自搜索结果中该文献详情页的编号）
字段铁律：没有确切信息的字段一律填 null（authors 填 []），禁止猜测、编造、补全。
只收录学术文献（期刊论文/学位论文/会议论文），报纸、杂志、网页资讯类一律剔除。
按相关度排序，最多 {n} 条。没有符合要求的结果时输出 []。"""


def _headers(api_key: str) -> dict:
    # 文档「接口定义」用 Authorization；curl 示例用 X-Appbuilder-Authorization——两个都带上兼容
    bearer = f"Bearer {api_key}"
    return {"Authorization": bearer, "X-Appbuilder-Authorization": bearer}


def _parse_json_array(text: str) -> list | None:
    """从模型输出提取 JSON 数组（容忍 markdown 围栏/前后杂文）；失败返回 None。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    start = t.find("[")
    end = t.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


class QianfanScholarAdapter:
    """百度学术（千帆）中文文献检索：A2c 主 + A2b 降级。"""

    id = "qianfan_scholar"
    label = "百度学术"

    def _api_key(self) -> str:
        return (web_search_config().get("qianfan") or {}).get("api_key") or ""

    def search(
        self,
        query: str,
        max_results: int,
        *,
        query_orig: str | None = None,
        lang: str | None = None,
    ) -> list[WebResult]:
        # 中文通道：query_orig（中文原词）优先；无则退用 query（英文直输场景）
        q = (query_orig or query).strip()
        if not q:
            return []
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("web_search.qianfan.api_key 未配置")

        results = self._search_a2c(q, max_results, api_key)
        if results is not None:
            return results
        logger.info(f"[websearch] A2c 不可用，降级 A2b 纯搜索: {q!r}")
        return self._search_a2b(q, max_results, api_key)

    # ---- A2c 智能搜索生成（主）----

    def _search_a2c(self, q: str, max_results: int, api_key: str) -> list[WebResult] | None:
        """返回 None = 主通道不可用（调用方降级 A2b）；返回 [] = 正常但无命中。"""
        payload = {
            "messages": [{"role": "user", "content": q}],
            "model": _MODEL,
            "stream": False,
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": min(20, max(10, max_results))}],
            "search_filter": {"match": {"site": _SITE}},
            "instruction": _INSTRUCTION.format(n=max_results),
            "enable_corner_markers": False,
        }
        res = post_json(
            f"{_BASE}/chat/completions",
            payload,
            timeout=_TIMEOUT_A2C,
            headers=_headers(api_key),
            redact=[api_key],
        )
        if not res.ok or not isinstance(res.data, dict):
            logger.warning(f"[websearch] A2c 请求失败: {res.error or '响应非 JSON'}")
            return None
        data = res.data
        if data.get("code"):  # 业务错误码（鉴权/限流/模型未开通等）
            logger.warning(f"[websearch] A2c 业务错误 {data.get('code')}: {data.get('message')}")
            return None
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("[websearch] A2c 响应缺 choices，降级")
            return None
        items = _parse_json_array(content)
        if items is None:
            logger.warning("[websearch] A2c 输出非 JSON 数组，降级")
            return None

        # ref_id → references 详情页 URL
        ref_urls: dict[int, str] = {}
        for ref in data.get("references") or []:
            try:
                ref_urls[int(ref.get("id"))] = ref.get("url") or ""
            except (TypeError, ValueError):
                continue

        results: list[WebResult] = []
        for it in items[:max_results]:
            if not isinstance(it, dict) or not it.get("title"):
                continue
            ref_id = it.get("ref_id")
            url = ""
            if isinstance(ref_id, int):
                url = ref_urls.get(ref_id, "")
            results.append(
                WebResult(
                    title=str(it["title"]),
                    source=self.label,
                    authors=[str(a) for a in (it.get("authors") or [])][:10],
                    journal=it.get("journal") or None,
                    year=str(it["year"]) if it.get("year") else None,
                    abstract=it.get("abstract") or None,
                    doi=None,  # A2c 无 DOI（核实清单已知局限）
                    url=url,
                    oa_url=None,
                )
            )
        return results

    # ---- A2b 纯搜索（降级）----

    def _search_a2b(self, q: str, max_results: int, api_key: str) -> list[WebResult]:
        payload = {
            "messages": [{"role": "user", "content": q}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": min(20, max_results)}],
            "search_filter": {"match": {"site": _SITE}},
        }
        res = post_json(
            f"{_BASE}/web_search",
            payload,
            timeout=_TIMEOUT_A2B,
            headers=_headers(api_key),
            redact=[api_key],
        )
        if not res.ok or not isinstance(res.data, dict):
            logger.warning(f"[websearch] A2b 请求失败: {res.error or '响应非 JSON'}")
            return []
        results: list[WebResult] = []
        for ref in (res.data.get("references") or [])[:max_results]:
            if ref.get("type") not in (None, "web"):
                continue  # 剔除图片/视频等非网页模态
            title = (ref.get("title") or "").strip()
            if not title:
                continue
            results.append(
                WebResult(
                    title=title,
                    source=self.label,
                    authors=[],
                    journal=None,
                    year=None,
                    abstract=(ref.get("content") or "").strip()[:300] or None,
                    doi=None,
                    url=ref.get("url") or "",
                    oa_url=None,
                    note=NOTE_DEGRADED,
                )
            )
        return results
