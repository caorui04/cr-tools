"""GgufProvider — llama.cpp server 或 Ollama HTTP 调用。

7B~9B Q4 GGUF 本地推理。
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


class GgufProvider:
    """基于 llama.cpp server 或 Ollama 的本地 LLM 提供者。"""

    name: str

    def __init__(
        self,
        model_name: str,
        server: str = "llama-server",
        port: int = 8085,
        ctx_size: int = 8192,
    ):
        self.name = f"gguf-{model_name}"
        self._server = server  # "llama-server" | "ollama"
        self._port = port
        self._ctx_size = ctx_size
        self._model_name = model_name

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    # ---- translate ----

    def translate(self, text: str, src: str, tgt: str) -> str:
        """HTTP POST 到 llama.cpp /completion 或 Ollama /api/generate。"""
        template = load_template("translate")
        prompt = render_template(template, text=text, src=src, tgt=tgt)

        if self._server == "ollama":
            return self._ollama_generate(prompt)
        else:
            return self._llamacpp_completion(prompt)

    def _llamacpp_completion(self, prompt: str) -> str:
        """llama.cpp server /completion。

        n_predict 取 min(ctx_size, 1024)：防御长生成占死唯一 slot
        （llama-server 默认单 slot 串行，一次不输出 EOS 的长生成会让
        后续所有请求排队超时）。
        """
        try:
            resp = httpx.post(
                f"{self.base_url}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": min(self._ctx_size, 1024),
                    "temperature": 0.1,
                    "stop": ["</s>", "<|endoftext|>"],
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["content"]
        except httpx.TimeoutException as e:
            raise LLMError(f"GGUF 翻译超时 ({_TIMEOUT}s)", retryable=True) from e
        except httpx.HTTPStatusError as e:
            retryable = e.response.status_code >= 500
            raise LLMError(
                f"GGUF 翻译 HTTP {e.response.status_code}", retryable=retryable
            ) from e
        except (httpx.RequestError, KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"GGUF 翻译请求失败: {e}", retryable=True) from e

    def _ollama_generate(self, prompt: str) -> str:
        """Ollama /api/generate。"""
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self._model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()["response"]
        except httpx.TimeoutException as e:
            raise LLMError(f"Ollama 翻译超时 ({_TIMEOUT}s)", retryable=True) from e
        except httpx.HTTPStatusError as e:
            retryable = e.response.status_code >= 500
            raise LLMError(
                f"Ollama 翻译 HTTP {e.response.status_code}", retryable=retryable
            ) from e
        except (httpx.RequestError, KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"Ollama 翻译请求失败: {e}", retryable=True) from e

    # ---- extract_biblio ----

    def extract_biblio(self, firstpage_text: str, format: str) -> dict:
        """HTTP POST，加载 extract_biblio prompt 模板。

        解析失败重试 1 次后返回全 null + 0 置信度。
        """
        template = load_template("extract_biblio")
        prompt = render_template(
            template, firstpage_text=firstpage_text, format=format
        )

        for attempt in range(2):
            try:
                raw = self._call_for_json(prompt)
                return validate_biblio(raw)
            except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
                if attempt == 0:
                    logger.warning(f"extract_biblio 第 1 次失败，重试: {e}")
                else:
                    logger.error(f"extract_biblio 重试后仍失败，fallback 全 null: {e}")
                    return null_biblio()

        return null_biblio()

    def _call_for_json(self, prompt: str) -> dict[str, Any]:
        """调用 LLM 并解析为 JSON dict。"""
        if self._server == "ollama":
            text = self._ollama_generate(prompt)
        else:
            text = self._llamacpp_completion(prompt)
        # S8：围栏剥离 + 解析归 llm.json_utils
        return parse_json_obj(text)

    def _chat_for_json(self, system: str, user: str) -> dict[str, Any]:
        """双消息 JSON 调用（m6_digest / m6_cite duck-typing 协议；修 C1：此前缺失必降级）。

        形态对齐 llama_chat_provider._chat_for_json（system + JSON 输出约束 + user）；
        completion/generate 端点无 system 通道，拼接为单 prompt。
        返回解析后的 dict；失败抛 LLMError/JSONDecodeError 由调用方降级。
        """
        prompt = (
            system
            + "\n\n请仅输出 JSON，不要添加任何其他文字或解释。\n\n"
            + user
        )
        if self._server == "ollama":
            text = self._ollama_generate(prompt)
        else:
            text = self._llamacpp_completion(prompt)
        return parse_json_obj(text)

    # ---- 存活探测 ----

    def check_alive(self) -> bool:
        """探测 llama.cpp / Ollama 是否存活。"""
        try:
            if self._server == "ollama":
                resp = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            else:
                resp = httpx.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except httpx.RequestError:
            return False
