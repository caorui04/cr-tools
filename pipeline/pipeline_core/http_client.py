"""网络访问统一封装（U4 资产契约 2.1）。

管线一切出站请求收口到本模块（契约第 1 条：禁止业务模块自建 httpx 调用），
统一"显式超时 + 错误归一 + 单行日志 + 脱敏"四件事：

- NetResult 归一四类错误文案：超时 / 连接失败 / HTTP 错误码 / JSON 解析失败；
  任何 httpx 异常归一进 NetResult，不向调用方抛（契约第 4 条）；
- 单行日志 [http] → METHOD host/path?query | 耗时 | status；redact 指定的秘密值
  子串替换为 ***（语义对齐 proc.py 的 redact：子串替换、空串跳过）；
- data：Content-Type 为 JSON 时为解析后的 dict/list；非 JSON 响应为原始文本
  （arXiv Atom XML 等场景）。仅当响应声明 JSON 但解析失败时归一为
  "响应 JSON 解析失败"；
- web_search_enabled()：读权威 config.yaml 的 analysis.web_search_enabled，
  权威路径由包位置推导，禁止 Path.cwd()（修 C5：cwd 随调用方启动目录变化）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

logger = logging.getLogger(__name__)

# 权威 config.yaml：pipeline 包根目录（本文件位于 <root>/pipeline_core/http_client.py）。
# 禁止 Path.cwd()（契约第 3 条 / C5）：cwd 取决于调用方启动目录，不代表管线目录。
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class NetResult:
    """网络请求结果。ok = 拿到响应且 status < 400 且（声明 JSON 时）解析成功。"""

    ok: bool
    status: int | None  # HTTP 状态码；网络层失败（超时/连接失败）为 None
    data: dict | list | str | int | None  # JSON→dict/list；非 JSON→原始文本；download_file→字节数
    error: str  # 归一中文文案；ok 时为空串
    elapsed: float  # 秒


def _redact_text(text: str, redact: list[str] | None) -> str:
    """text 中 redact 列出的秘密值出现处替换为 ***（子串替换；空串秘密跳过）。

    与 proc.py._display_argv 同语义：秘密值可能嵌在 URL query / 错误详情里。
    """
    shown = text
    for s in redact or []:
        if s:
            shown = shown.replace(s, "***")
    return shown


def _request(
    method: str,
    url: str,
    *,
    timeout: int,
    params: dict | None = None,
    headers: dict | None = None,
    json_payload: dict | None = None,
    redact: list[str] | None = None,
) -> NetResult:
    """统一请求执行 + 归一。任何 httpx 异常归一进 NetResult，不上抛。"""
    u = urlsplit(url)
    shown = _redact_text(f"{u.netloc}{u.path}" + (f"?{u.query}" if u.query else ""), redact)
    t0 = time.monotonic()
    try:
        resp = httpx.request(
            method, url, timeout=timeout, params=params, headers=headers, json=json_payload
        )
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t0
        error = f"请求超时（{timeout}s）"
        logger.warning(f"[http] → {method} {shown} | {elapsed:.2f}s | status=- | {error}")
        return NetResult(ok=False, status=None, data=None, error=error, elapsed=elapsed)
    except httpx.HTTPError as e:
        # 连接失败/协议错误等传输层异常：错误详情可能含 URL（带秘密），脱敏后入文案
        elapsed = time.monotonic() - t0
        detail = _redact_text(str(e) or type(e).__name__, redact)
        error = f"连接失败（{detail}）"
        logger.warning(f"[http] → {method} {shown} | {elapsed:.2f}s | status=- | {error}")
        return NetResult(ok=False, status=None, data=None, error=error, elapsed=elapsed)

    elapsed = time.monotonic() - t0
    status = resp.status_code
    if status >= 400:
        error = f"HTTP 错误 {status}"
        logger.warning(f"[http] → {method} {shown} | {elapsed:.2f}s | status={status} | {error}")
        return NetResult(ok=False, status=status, data=None, error=error, elapsed=elapsed)

    if "json" in resp.headers.get("content-type", "").lower():
        try:
            data: dict | list | str = resp.json()
        except ValueError:
            error = "响应 JSON 解析失败"
            logger.warning(f"[http] → {method} {shown} | {elapsed:.2f}s | status={status} | {error}")
            return NetResult(ok=False, status=status, data=None, error=error, elapsed=elapsed)
    else:
        data = resp.text
    logger.info(f"[http] → {method} {shown} | {elapsed:.2f}s | status={status}")
    return NetResult(ok=True, status=status, data=data, error="", elapsed=elapsed)


def get_json(
    url: str,
    *,
    timeout: int = 30,
    params: dict | None = None,
    headers: dict | None = None,
) -> NetResult:
    """GET 请求归一封装。非 JSON 响应（如 XML）data 为原始文本。"""
    return _request("GET", url, timeout=timeout, params=params, headers=headers)


# 下载默认 UA：部分出版商 OA 直链对裸 httpx UA 返 403（侦察 §3），用浏览器 UA 兼容
_DOWNLOAD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def download_file(
    url: str,
    dest: Path,
    *,
    timeout: int = 60,
    max_bytes: int = 50 * 1024 * 1024,
    headers: dict | None = None,
    redact: list[str] | None = None,
) -> NetResult:
    """流式下载到 dest（U4 契约扩展，T8 OA 一键下载；错误归一 + 单行日志同 _request）。

    - 成功：NetResult(ok=True, data=实际字节数 int)；
    - HTTP 错误/超时/连接失败：同 _request 归一文案，不落半成品（临时文件清理）；
    - 超过 max_bytes：中断下载，归一 "文件过大" 错误（先写 .part 再改名，截断即弃）。
    """
    u = urlsplit(url)
    shown = _redact_text(f"{u.netloc}{u.path}" + (f"?{u.query}" if u.query else ""), redact)
    hdrs = {"User-Agent": _DOWNLOAD_UA, **(headers or {})}
    part = dest.with_suffix(dest.suffix + ".part")
    t0 = time.monotonic()
    try:
        with httpx.stream("GET", url, timeout=timeout, headers=hdrs, follow_redirects=True) as resp:
            status = resp.status_code
            if status >= 400:
                elapsed = time.monotonic() - t0
                error = f"HTTP 错误 {status}"
                logger.warning(f"[http] → GET {shown} | {elapsed:.2f}s | status={status} | {error}")
                return NetResult(ok=False, status=status, data=None, error=error, elapsed=elapsed)
            total = 0
            with open(part, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        elapsed = time.monotonic() - t0
                        error = f"文件过大（超过 {max_bytes // (1024 * 1024)}MB 上限）"
                        logger.warning(f"[http] → GET {shown} | {elapsed:.2f}s | status={status} | {error}")
                        return NetResult(ok=False, status=status, data=None, error=error, elapsed=elapsed)
                    f.write(chunk)
        part.replace(dest)
        elapsed = time.monotonic() - t0
        logger.info(f"[http] → GET {shown} | {elapsed:.2f}s | status={status} | {total}B → {dest.name}")
        return NetResult(ok=True, status=status, data=total, error="", elapsed=elapsed)
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t0
        error = f"请求超时（{timeout}s）"
        logger.warning(f"[http] → GET {shown} | {elapsed:.2f}s | status=- | {error}")
        return NetResult(ok=False, status=None, data=None, error=error, elapsed=elapsed)
    except httpx.HTTPError as e:
        elapsed = time.monotonic() - t0
        detail = _redact_text(str(e) or type(e).__name__, redact)
        error = f"连接失败（{detail}）"
        logger.warning(f"[http] → GET {shown} | {elapsed:.2f}s | status=- | {error}")
        return NetResult(ok=False, status=None, data=None, error=error, elapsed=elapsed)
    finally:
        part.unlink(missing_ok=True)


def post_json(
    url: str,
    payload: dict,
    *,
    timeout: int = 30,
    headers: dict | None = None,
    redact: list[str] | None = None,
) -> NetResult:
    """POST JSON 请求归一封装。redact 秘密值在日志/错误文案中替换为 ***。"""
    return _request("POST", url, timeout=timeout, headers=headers, json_payload=payload, redact=redact)


def web_search_enabled() -> bool:
    """读权威 config.yaml 的 analysis.web_search_enabled（默认 True）。

    analysis 键未建模进 PipelineConfig dataclass（既有 routes.py 同款注释），故与
    config.py 加载方式一致直接 yaml.safe_load 原文；读取异常按默认开启处理
    （与迁移前 except Exception: pass 同语义）。路径权威来源为 _CONFIG_PATH（修 C5）。
    """
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return bool((raw.get("analysis") or {}).get("web_search_enabled", True))
    except Exception:
        return True
