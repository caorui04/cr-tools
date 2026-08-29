"""provider 公共 JSON 工具（S8，批次 P）。

4 个 provider（llama_chat / gguf / cloud / stub）重复的公共逻辑收拢：
- ``strip_json_fence`` / ``parse_json_obj``：模型输出 markdown 围栏剥离 + JSON 解析
  （三处 ``_chat_for_json`` / ``_call_for_json`` 同一形态）；
- ``validate_biblio``：extract_biblio 输出结构校验补全
  （biblio 11 键 + field_confidence 9 键，缺失填 null / 0.0）；
- ``null_biblio``：全 null + 0 置信度兜底（解析重试失败后的统一降级结构）。

行为与各 provider 原实现逐字节等价；仅收拢，不改语义。
"""

from __future__ import annotations

import json
from typing import Any

# extract_biblio 输出契约：biblio 11 键
BIBLIO_KEYS = [
    "authors", "title", "type", "venue", "year",
    "volume", "issue", "pages", "doi", "publisher", "url",
]
# field_confidence 9 键（不含 publisher/url，与原三处实现一致）
CONF_KEYS = [
    "authors", "title", "type", "venue", "year",
    "volume", "issue", "pages", "doi",
]


def strip_json_fence(text: str) -> str:
    """剥离 markdown 代码围栏（```json ...``` / 裸 ```...```），其余原样返回。"""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text


def parse_json_obj(text: str) -> dict[str, Any]:
    """围栏剥离 + json.loads。坏 JSON 抛 JSONDecodeError，由调用方按自身语义降级。"""
    return json.loads(strip_json_fence(text))


def validate_biblio(raw: dict | str) -> dict:
    """校验 extract_biblio 输出结构，缺失字段填 null + 0 置信度。"""
    if isinstance(raw, str):
        raw = json.loads(raw)
    biblio = raw.get("biblio", {})
    confidence = raw.get("field_confidence", {})
    return {
        "biblio": {k: biblio.get(k) for k in BIBLIO_KEYS},
        "field_confidence": {k: confidence.get(k, 0.0) for k in CONF_KEYS},
    }


def null_biblio() -> dict:
    """全 null biblio + 全 0 field_confidence（统一降级结构）。"""
    return {
        "biblio": {k: None for k in BIBLIO_KEYS},
        "field_confidence": {k: 0.0 for k in CONF_KEYS},
    }
