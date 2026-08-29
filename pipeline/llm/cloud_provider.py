"""CloudApiProvider — 云端 LLM 调用，支持双协议。

默认适配 DeepSeek，支持两种调用模式（api_style 配置切换）：
- responses（推荐，DeepSeek V4 官方：https://api-docs.deepseek.com/zh-cn/guides/responses_api）
  POST {base}/v1/responses，body 用 instructions/input，响应取 output_text。
- chat_completions（OpenAI 兼容传统模式，适配 GLM/Kimi 等第三方）
  POST {base}/v1/chat/completions，messages=[system,user]。

统一入口 _chat(system, user)：translate / extract_biblio / _chat_for_json 均基于它，
api_style 只影响传输层。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .errors import LLMError
from .json_utils import null_biblio, parse_json_obj, validate_biblio
from .template_loader import load_template, render_template

logger = logging.getLogger(__name__)

_TIMEOUT = 120.0


class CloudApiProvider:
    """基于 OpenAI/DeepSeek 兼容 API 的云端 LLM 提供者。"""

    name: str

    def __init__(
        self,
        base_url: str = "https://api.deepseek.com",
        api_key: str | None = None,
        api_key_env: str = "DEEPSEEK_API_KEY",
        model: str = "deepseek-chat",
        api_style: str = "chat_completions",
    ):
        self.name = f"cloud-{model}"
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or os.environ.get(api_key_env, "")
        self._model = model
        self._api_style = api_style

    # ---- 统一 Chat 入口 ----

    def _chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> str:
        """按 api_style 调用云端并返回模型文本。失败抛 LLMError（调用方降级）。"""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._api_style == "responses":
                body: dict[str, Any] = {
                    "model": self._model,
                    "instructions": system,
                    "input": user,
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                }
                if json_mode:
                    body["text"] = {"format": {"type": "json_object"}}
                resp = httpx.post(
                    f"{self._base_url}/v1/responses",
                    headers=headers,
                    json=body,
                    timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                # 官方结构：response.output_text 便捷字段；缺失时从 output 数组拼
                text = data.get("output_text") or ""
                if not text:
                    parts = []
                    for it in data.get("output") or []:
                        if it.get("type") == "message":
                            for c in it.get("content") or []:
                                if c.get("type") == "output_text":
                                    parts.append(c.get("text", ""))
                    text = "".join(parts)
                return text

            # chat_completions（默认/兼容）
            body = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as e:
            raise LLMError(f"Cloud 超时 ({_TIMEOUT}s)", retryable=True) from e
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            retryable = code >= 500 or code == 429
            raise LLMError(f"Cloud HTTP {code}", retryable=retryable) from e
        except (httpx.RequestError, KeyError, json.JSONDecodeError, IndexError) as e:
            raise LLMError(f"Cloud 请求失败: {e}", retryable=True) from e

    # ---- translate ----

    def translate(self, text: str, src: str, tgt: str) -> str:
        """翻译：模板渲染为 system 提示，原文放 user。返回纯文本译文。"""
        template = load_template("translate")
        system_prompt = render_template(template, text=text, src=src, tgt=tgt)
        return self._chat(system_prompt, text, max_tokens=4096, temperature=0.1)

    # ---- extract_biblio ----

    def extract_biblio(self, firstpage_text: str, format: str) -> dict:
        """调用 LLM，要求输出 JSON。解析失败重试 1 次后 fallback 全 null。"""
        template = load_template("extract_biblio")
        prompt = render_template(
            template, firstpage_text=firstpage_text, format=format
        )

        for attempt in range(2):
            try:
                raw = self._call_for_json(prompt)
                # 补全结构
                return validate_biblio(raw)
            except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
                if attempt == 0:
                    logger.warning(f"Cloud extract_biblio 第 1 次失败，重试: {e}")
                else:
                    logger.error(
                        f"Cloud extract_biblio 重试后仍失败，fallback 全 null: {e}"
                    )
                    return null_biblio()

        return null_biblio()

    def _call_for_json(self, prompt: str) -> dict[str, Any]:
        """单 prompt 调用并要求 JSON 输出，解析为 dict。"""
        text = self._chat(
            "输出严格 JSON，格式见用户要求。仅输出 JSON，不要其他文字。",
            prompt,
            json_mode=True,
        )
        if isinstance(text, str):
            return json.loads(text)
        return text

    def _chat_for_json(self, system: str, user: str) -> dict[str, Any]:
        """双消息 Chat 调用并要求 JSON 输出（m6_digest / m6_cite 用，协议同 llama_chat_provider）。

        返回解析后的 dict；失败抛 LLMError 由调用方降级。
        """
        text = self._chat(
            system + "\n\n请仅输出 JSON，不要添加任何其他文字或解释。",
            user,
            json_mode=True,
        )
        # S8：围栏剥离 + 解析归 llm.json_utils（与 llama_chat_provider 一致，容忍 markdown 围栏）
        return parse_json_obj(text)
