"""统一结果 schema（蓝图第十六条 L209：标题/作者/期刊/年份/摘要/DOI/来源/OA 链接）。

所有检索源适配器产出本结构，框架/端点/前端只认本 schema——新增源不改框架。
url 与 oa_url 分离：url = 详情页/原文落地页（必有可空），oa_url = OA 全文直链
（仅 OA 命中时非空，T8 一键下载入库仅对 oa_url 开放，见规划 §6.1 L208）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class WebResult:
    """联网检索统一结果条目。无信息字段一律 None/空串，不瞎编（字段铁律对齐 A2c）。"""

    title: str
    source: str  # 来源标签（适配器 label，U4 来源声明契约）
    authors: list[str] = field(default_factory=list)
    journal: str | None = None
    year: str | None = None
    abstract: str | None = None
    doi: str | None = None
    url: str = ""  # 详情页/落地页链接
    oa_url: str | None = None  # OA 全文直链（可下载标记）
    note: str | None = None  # 结果级提示（如「降级：网页片段结果」；T7 A2b 降级标记）

    def to_dict(self) -> dict:
        return asdict(self)
