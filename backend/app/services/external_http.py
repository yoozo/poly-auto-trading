from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 2.0


DEFAULT_RETRY_POLICY = RetryPolicy()


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    retryable: Callable[[Exception], bool] | None = None,
) -> T:
    """统一外部 API 重试入口，调用方仍负责服务健康和业务日志。"""
    if policy.attempts <= 1:
        return await operation()
    should_retry = retryable or is_retryable_http_error
    last_error: Exception | None = None
    for attempt in range(policy.attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt >= policy.attempts - 1 or not should_retry(exc):
                raise
            await asyncio.sleep(min(policy.max_delay, policy.base_delay * (2**attempt)))
    assert last_error is not None
    raise last_error


def is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}
    return False
