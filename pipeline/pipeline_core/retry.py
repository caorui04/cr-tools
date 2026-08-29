"""通用重试装饰器。

指数退避；达到上限抛 RetryExhaustedError。
retry_count 递增由调用方负责写入 sidecar（底座不解耦调用方逻辑）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


class RetryExhaustedError(Exception):
    """重试耗尽异常，携带最后一次错误。"""

    def __init__(self, message: str, last_error: Exception | None = None):
        super().__init__(message)
        self.last_error = last_error


def retry_on_failure(
    max_retries: int = 3,
    backoff_s: float = 1.0,
    backoff_multiplier: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """装饰器：函数失败自动重试。

    Args:
        max_retries: 最多重试次数（不含首次执行）
        backoff_s: 首次退避等待秒数
        backoff_multiplier: 退避倍增因子
        retryable_exceptions: 可重试的异常类型元组

    Returns:
        装饰后的函数。
    """
    if max_retries < 1:
        raise ValueError("max_retries 必须 >= 1")

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            delay = backoff_s

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            f"{func.__name__} 第 {attempt + 1} 次失败（共 {max_retries + 1} 次尝试），"
                            f"{delay:.1f}s 后重试: {e}"
                        )
                        time.sleep(delay)
                        delay *= backoff_multiplier
                    else:
                        logger.error(
                            f"{func.__name__} 全部 {max_retries + 1} 次尝试失败，最后错误: {e}"
                        )

            raise RetryExhaustedError(
                f"{func.__name__} 重试 {max_retries} 次后仍失败", last_error
            )

        return wrapper

    return decorator
