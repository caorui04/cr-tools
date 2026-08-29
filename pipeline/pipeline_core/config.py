"""配置加载与校验。

从 config.yaml 加载 PipelineConfig，缺失键填充默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """配置加载或校验失败。"""


@dataclass
class Thresholds:
    page_text_min_chars: int = 50
    image_area_ratio: float = 0.70
    doc_type_ratio: float = 0.90
    lang_foreign_ratio: float = 0.80
    block_min_chars: int = 50
    ocr_confidence_min: float = 0.85
    sample_pages: list[int] = field(default_factory=lambda: [5, 2, 2])


@dataclass
class Limits:
    max_pages_warn: int = 500
    retry_max: int = 3
    worker_memory_gb: int = 4


@dataclass
class GgufConfig:
    model_path: str = "<可配置，首跑下载>"
    server: str = "llama-server"
    port: int = 8085
    ctx_size: int = 8192


@dataclass
class CloudConfig:
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-chat"
    # 新增字段（默认值保持向后兼容）：
    # api_key 直配（优先于环境变量 api_key_env）；api_style = responses | chat_completions
    api_key: str = ""
    api_style: str = "chat_completions"


@dataclass
class LLMConfig:
    prefer: str = "gguf"
    allow_cloud_fallback: bool = True
    gguf: GgufConfig = field(default_factory=GgufConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)


@dataclass
class ModelsConfig:
    ocr_path: str = "<可配置>"
    embedding_path: str = "<可配置>"
    llm: LLMConfig = field(default_factory=LLMConfig)
    # 2026-08-01：内存调度配置（模型按需加载/保活停机）
    scheduler: dict = field(default_factory=lambda: {"memory_budget_mb": 8000, "idle_stop_s": 600})


@dataclass
class ScheduleConfig:
    heavy_tasks_night_only: bool = False


@dataclass
class PipelineConfig:
    """管线全局配置。"""

    pipeline_root: Path
    http_port: int = 8737
    thresholds: Thresholds = field(default_factory=Thresholds)
    limits: Limits = field(default_factory=Limits)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    # pandoc 可执行文件路径（草稿保存生成阶段 docx / docx 草稿导入转换；缺失则降级跳过）
    pandoc_path: str = ""


# ---- 加载入口 ----


def _deep_update(base: dict, override: dict) -> dict:
    """递归合并 override 到 base，返回新 dict。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = value
    return result


def _dict_to_config(data: dict) -> PipelineConfig:
    """将嵌套 dict 转换为 PipelineConfig 数据类。"""
    thresholds = Thresholds(**data.get("thresholds", {}))
    limits = Limits(**data.get("limits", {}))

    models_raw = data.get("models", {})
    llm_raw = models_raw.get("llm", {})
    gguf = GgufConfig(**llm_raw.get("gguf", {}))
    cloud = CloudConfig(**llm_raw.get("cloud", {}))
    llm = LLMConfig(
        prefer=llm_raw.get("prefer", "gguf"),
        allow_cloud_fallback=llm_raw.get("allow_cloud_fallback", True),
        gguf=gguf,
        cloud=cloud,
    )
    models = ModelsConfig(
        ocr_path=models_raw.get("ocr_path", "<可配置>"),
        embedding_path=models_raw.get("embedding_path", "<可配置>"),
        llm=llm,
        scheduler=models_raw.get("scheduler", {"memory_budget_mb": 8000, "idle_stop_s": 600}),
    )

    schedule = ScheduleConfig(**data.get("schedule", {}))

    return PipelineConfig(
        pipeline_root=Path(data["pipeline_root"]),
        http_port=data.get("http_port", 8737),
        thresholds=thresholds,
        limits=limits,
        models=models,
        schedule=schedule,
        pandoc_path=data.get("pandoc_path", ""),
    )


def load_config(path: Path | None = None) -> PipelineConfig:
    """加载 config.yaml 并校验，缺失键填充默认值。

    默认查找顺序：1) 传入 path  2) <cwd>/config.yaml  3) 返回全默认配置。
    """
    if path is None:
        cwd_config = Path.cwd() / "config.yaml"
        if cwd_config.exists():
            path = cwd_config

    if path is None or not path.exists():
        # 全默认：pipeline_root 推导为当前目录下的 user-data/pipeline/
        default_root = Path.cwd() / "user-data" / "pipeline"
        return PipelineConfig(pipeline_root=default_root)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e

    if raw is None:
        raw = {}

    # 以默认值为底，用户配置覆盖
    defaults: dict[str, Any] = {
        "pipeline_root": str(Path.cwd() / "user-data" / "pipeline"),
        "http_port": 8737,
        "thresholds": {
            "page_text_min_chars": 50,
            "image_area_ratio": 0.70,
            "doc_type_ratio": 0.90,
            "lang_foreign_ratio": 0.80,
            "block_min_chars": 50,
            "ocr_confidence_min": 0.85,
            "sample_pages": [5, 2, 2],
        },
        "limits": {"max_pages_warn": 500, "retry_max": 3, "worker_memory_gb": 4},
        "models": {
            "ocr_path": "<可配置>",
            "embedding_path": "<可配置>",
            "scheduler": {"memory_budget_mb": 8000, "idle_stop_s": 600},
            "llm": {
                "prefer": "gguf",
                "allow_cloud_fallback": True,
                "gguf": {
                    "model_path": "<可配置，首跑下载>",
                    "server": "llama-server",
                    "port": 8085,
                    "ctx_size": 8192,
                },
                "cloud": {
                    "base_url": "https://api.deepseek.com",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "model": "deepseek-chat",
                },
            },
        },
        "schedule": {"heavy_tasks_night_only": False},
    }
    merged = _deep_update(defaults, raw)

    if "pipeline_root" not in merged or not merged["pipeline_root"]:
        merged["pipeline_root"] = str(Path.cwd() / "user-data" / "pipeline")

    config = _dict_to_config(merged)

    # 数值范围校验
    if config.thresholds.page_text_min_chars < 0:
        raise ConfigError("page_text_min_chars 不能为负数")
    if not (0 < config.thresholds.image_area_ratio <= 1):
        raise ConfigError("image_area_ratio 必须在 (0, 1] 范围内")
    if config.limits.retry_max < 1:
        raise ConfigError("retry_max 必须 >= 1")

    return config
