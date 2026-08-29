"""模拟适配器（T5 演示路径用）：返回固定假数据，验证「新增适配器不改框架代码」。

经 config.yaml web_search.adapters: ["mock"] 启用。不出网、不读 Key，
仅用于走查适配层接线（注册 → 扇出 → 统一 schema → 来源标签）与前端展示。
标题回显 query（走查时可直观确认「确实按新词重新检索了」，2026-08-12 走查反馈：
固定标题导致改词后结果看起来不变，误判检索未重发）。
真实源适配器落地后，生产配置不应启用本适配器。
"""

from __future__ import annotations

import dataclasses

from ..schema import WebResult


class MockAdapter:
    """固定假数据源。来源标签「Mock 模拟源」，走查时一眼可辨。"""

    id = "mock"
    label = "Mock 模拟源"

    _FIXED = [
        WebResult(
            title="Mock Result: Rural Revitalization and Media Convergence",
            source=label,
            authors=["Zhang, Wei", "Li, Na"],
            journal="Journal of Mock Studies",
            year="2024",
            abstract="Fixed mock abstract for adapter-layer walkthrough. Not a real paper.",
            doi="10.0000/mock.2024.001",
            url="https://example.com/mock/001",
            oa_url="https://example.com/mock/001.pdf",
        ),
        WebResult(
            title="Mock Result: A Second Entry Without OA Link",
            source=label,
            authors=["Wang, Fang"],
            journal=None,
            year="2023",
            abstract=None,
            doi=None,
            url="https://example.com/mock/002",
            oa_url=None,
        ),
    ]

    def search(
        self,
        query: str,
        max_results: int,
        *,
        query_orig: str | None = None,
        lang: str | None = None,
    ) -> list[WebResult]:
        """固定数据（截到 max_results），标题前缀回显实际检索词。"""
        return [
            dataclasses.replace(r, title=f"[{query}] {r.title}")
            for r in self._FIXED[:max_results]
        ]

