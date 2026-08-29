"""Pydantic 请求/响应模型。字段以 C3 为准。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubmitRequest(BaseModel):
    paths: list[str]
    translate: bool = False
    translate_paths: list[str] | None = None


class SubmitResult(BaseModel):
    path: str
    queued_name: str


class RejectResult(BaseModel):
    path: str
    reason: str


class SubmitResponse(BaseModel):
    accepted: list[SubmitResult] = []
    rejected: list[RejectResult] = []
    warnings: list[RejectResult] | None = None


class StatusResponse(BaseModel):
    api_version: str = "1.0"
    uptime_s: int = 0
    queues: dict[str, int] = Field(default_factory=dict)
    processing: list[str] = []
    # E-1：待处理队列文件列表（[损坏] 前缀标记损坏文件）
    pending: list[str] = []
    manifest_version: int = 0
    chunks_total: int = 0
    docs_total: int = 0
    # 各环节进度条（progress/<stage>.json）：{"m1": {"doc_id","done","total","updated_at"}|null, ...}
    stages: dict[str, dict | None] = Field(default_factory=dict)


class RetryRequest(BaseModel):
    doc_id: str | None = None
    all: bool = False


class RetryResponse(BaseModel):
    moved: int


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    doc_hash: str | None = None
    paper_id: str | None = None  # T1：对照项目 inputs_log 标「本项目资料」徽标


class SearchResultItem(BaseModel):
    chunk_id: str
    doc_hash: str
    text: str
    score: float
    doc_meta: dict
    is_project_input: bool = False  # T1：结果 doc 属于当前项目 inputs_log → 前端标「本项目资料」


class SearchResponse(BaseModel):
    results: list[SearchResultItem] = []
    query_en: str | None = None  # Q4：本地检索联合的英译词（None = 未联合/翻译不可用）


class CiteRequest(BaseModel):
    doc_id: str
    format: str = "GB/T 7714-2015"


class CiteResponse(BaseModel):
    draft: dict = Field(default_factory=dict)
    field_confidence: dict = Field(default_factory=dict)
    formatted: str = ""


class BibliographyRequest(BaseModel):
    bibliography: dict
