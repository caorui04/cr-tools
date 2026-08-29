"""StubProvider — 桩实现，联调用。

translate: 原样返回 + [stub] 前缀
extract_biblio: 返回全 null + 0 置信度（结构归 llm.json_utils.null_biblio，S8）
"""

from __future__ import annotations

from .json_utils import null_biblio


class StubProvider:
    """桩提供者，不依赖任何外部模型。"""

    name = "stub"

    def translate(self, text: str, src: str, tgt: str) -> str:
        """原样返回，前缀 [stub]。"""
        return f"[stub] {text}"

    def extract_biblio(self, firstpage_text: str, format: str) -> dict:
        """返回全 null biblio + 全 0 field_confidence。"""
        return null_biblio()
