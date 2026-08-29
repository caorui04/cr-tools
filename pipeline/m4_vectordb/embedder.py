"""BGE-M3 向量编码器（llama.cpp /embedding 接口）。

通过 HTTP 调用本地 llama-server 的 /embedding 端点。
支持两种模式：
  - llama.cpp 原生: POST /embedding {"content": "text"}
  - OpenAI 兼容:   POST /v1/embeddings {"input": "text"}

用法:
  embedder = GGUFEmbedder(base_url="http://127.0.0.1:8083")
  vec = embedder.encode_query("查询文本")
  vecs = embedder.encode(["文本1", "文本2"])
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0
_EMBEDDING_DIM = 1024  # BGE-M3 标准维度


class GGUFEmbedder:
    """通过 llama.cpp /embedding 端点的向量编码器。"""

    name = "bge-m3-gguf"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8083",
        use_openai_api: bool = False,
    ):
        self._base_url = base_url.rstrip("/")
        self._use_openai = use_openai_api

    def check_alive(self) -> bool:
        """探测 llama-server 是否存活。"""
        try:
            resp = httpx.get(f"{self._base_url}/health", timeout=5)
            return resp.status_code == 200
        except httpx.RequestError:
            return False

    def encode(
        self, texts: list[str], batch_size: int = 8
    ) -> list[list[float]]:
        """批量编码文本。"""
        embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            for text in batch:
                vec = self._encode_one(text)
                if vec:
                    embeddings.append(vec)
                else:
                    # fallback: zero vector
                    embeddings.append([0.0] * _EMBEDDING_DIM)
        return embeddings

    def encode_query(self, query: str) -> list[float] | None:
        """编码查询文本（单条）。

        返回 None 表示嵌入服务不可用（不再 fallback 零向量——零向量会
        导致检索返回 score=1.0 的假匹配，表现为"检索不出正确资料"）。
        调用方须检测 None 并明确报错。
        """
        return self._encode_one(query)

    def _encode_one(self, text: str) -> list[float] | None:
        """调用 embedding API 编码单条文本。"""
        if not text.strip():
            return [0.0] * _EMBEDDING_DIM

        try:
            if self._use_openai:
                return self._call_openai(text)
            else:
                return self._call_native(text)
        except Exception as e:
            logger.warning(f"embedding 调用失败: {e}")
            return None

    def _call_native(self, text: str) -> list[float]:
        """llama.cpp 原生 /embedding 端点。
        
        响应格式（新版）: [{"index":0, "embedding": [[float,...]]}]
        响应格式（旧版）: {"embedding": [float,...]}
        """
        resp = httpx.post(
            f"{self._base_url}/embedding",
            json={"content": text},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # 兼容新旧格式
        if isinstance(data, list) and len(data) > 0:
            emb_list = data[0].get("embedding", [[]])
            emb = emb_list[0] if emb_list else []
        else:
            emb = data.get("embedding", [])
        return _normalize(emb)

    def _call_openai(self, text: str) -> list[float]:
        """OpenAI 兼容 /v1/embeddings 端点。"""
        resp = httpx.post(
            f"{self._base_url}/v1/embeddings",
            json={"input": text, "model": "bge-m3"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        emb = data["data"][0]["embedding"]
        return _normalize(emb)


def _normalize(vec: list[float]) -> list[float]:
    """L2 归一化（与 sentence-transformers normalize_embeddings 一致）。"""
    import math
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


# 兼容别名：07-27 架构统一前的旧类名（routes/indexer 等处仍按此名导入）
Embedder = GGUFEmbedder
