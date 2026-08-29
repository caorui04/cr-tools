"""Prompt 模板加载器。

模板文件置于 llm/prompts/ 目录，便于校准。
"""

from __future__ import annotations

from pathlib import Path

_cache: dict[str, str] = {}


def _prompts_dir() -> Path:
    """返回 prompts/ 目录绝对路径。"""
    return Path(__file__).resolve().parent / "prompts"


def load_template(name: str) -> str:
    """加载 prompts/<name>.txt 模板内容（缓存）。"""
    if name not in _cache:
        path = _prompts_dir() / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"模板文件不存在: {path}")
        _cache[name] = path.read_text(encoding="utf-8")
    return _cache[name]


def render_template(template: str, **kwargs: str) -> str:
    """简单模板渲染：{key} 替换。

    Args:
        template: 模板字符串
        **kwargs: 键值对

    Returns:
        渲染后字符串。
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace(f"{{{key}}}", value)
    return result
