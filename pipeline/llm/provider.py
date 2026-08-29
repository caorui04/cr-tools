"""LLMProvider 协议定义。

所有 LLM 实现必须遵循此接口。
"""

from typing import Protocol


class LLMProvider(Protocol):
    """LLM 推理提供者协议。

    M3 翻译使用 translate()，M6 著录补全使用 extract_biblio()。
    """

    name: str

    def translate(self, text: str, src: str, tgt: str) -> str:
        """翻译文本，保持段落结构与 MD 标记。

        Args:
            text: 待翻译文本
            src: 源语言 BCP-47 简写（如 "en"）
            tgt: 目标语言 BCP-47 简写（如 "zh"）

        Returns:
            翻译后文本。
        """
        ...

    def extract_biblio(self, firstpage_text: str, format: str) -> dict:
        """从首页文本提取文献著录信息。

        Args:
            firstpage_text: sidecar/<doc_id>_firstpage.txt 内容
            format: 引用格式（"GB/T 7714-2015" | "APA" | "MLA" | "Chicago"）

        Returns:
            C6 BiblioDraft 结构（不含 formatted）。
        """
        ...
