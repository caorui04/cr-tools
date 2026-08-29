"""LlamaCppChatProvider — llama.cpp /v1/chat/completions (OpenAI 兼容)。

适用于指令模型（Qwen2.5、Llama 等），通过 /v1/chat/completions 端点调用。
与 CloudApiProvider 同接口，但面向本地 llama-server。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .errors import LLMError
from .json_utils import null_biblio, parse_json_obj, validate_biblio
from .template_loader import load_template, render_template

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0


class LlamaCppChatProvider:
    """通过 llama.cpp /v1/chat/completions 的本地 LLM 提供者。

    适用于指令遵循模型（Qwen2.5-3B 等），比 /completion 端点更适合
    结构化 JSON 输出、多轮对话等场景。
    """

    name: str

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8082",
        model_name: str = "local-model",
    ):
        self.name = f"llama-cpp-{model_name}"
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name

    def check_alive(self) -> bool:
        """探测 llama-server 是否存活。"""
        try:
            resp = httpx.get(f"{self._base_url}/health", timeout=5)
            return resp.status_code == 200
        except httpx.RequestError:
            return False

    # ---- translate ----

    def translate(self, text: str, src: str, tgt: str) -> str:
        """翻译文本（回退到 /completion，Hy-MT2 建议用 GgufProvider）。"""
        template = load_template("translate")
        prompt = render_template(template, text=text, src=src, tgt=tgt)
        return self._chat("你是一个专业的学术论文翻译助手。保持 Markdown 格式和术语准确性。", prompt)

    # ---- extract_biblio ----

    def extract_biblio(self, firstpage_text: str, format: str) -> dict:
        """从首页文本提取文献著录信息（使用 chat 格式 + JSON 约束）。"""
        template = load_template("extract_biblio")
        prompt = render_template(
            template, firstpage_text=firstpage_text, format=format
        )

        for attempt in range(2):
            try:
                raw = self._chat_for_json(
                    "你是一个学术文献著录专家。严格按照 GB/T 7714-2015 标准提取文献信息。",
                    prompt,
                )
                return validate_biblio(raw)
            except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
                if attempt == 0:
                    logger.warning(f"extract_biblio 第 1 次失败，重试: {e}")
                else:
                    logger.error(f"extract_biblio 重试后仍失败，fallback: {e}")
                    return null_biblio()

        return null_biblio()

    # ---- low-level chat ----

    def _chat(self, system: str, user: str) -> str:
        """单轮 chat 调用。"""
        try:
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                json={
                    "model": self._model_name,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as e:
            raise LLMError(f"llama.cpp 超时 ({_TIMEOUT}s)", retryable=True) from e
        except httpx.HTTPStatusError as e:
            retryable = e.response.status_code >= 500
            raise LLMError(f"llama.cpp HTTP {e.response.status_code}", retryable=retryable) from e
        except (httpx.RequestError, KeyError, json.JSONDecodeError, IndexError) as e:
            raise LLMError(f"llama.cpp 请求失败: {e}", retryable=True) from e

    def _chat_for_json(self, system: str, user: str) -> dict[str, Any]:
        """Chat 调用并要求 JSON 输出。"""
        text = self._chat(
            system + "\n\n请仅输出 JSON，不要添加任何其他文字或解释。",
            user,
        )
        # S8：围栏剥离 + 解析归 llm.json_utils
        return parse_json_obj(text)
