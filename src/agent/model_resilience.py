"""模型调用重试与降级的共享策略。

主 Agent 和子 Agent 的模型调用都走 LangChain 内置的
``ModelRetryMiddleware`` / ``ModelFallbackMiddleware``：

- 先按可重试错误分类做有限退避重试；
- 重试耗尽后切到备用模型；
- 不可重试错误（400/401/403 等）不重试，直接交给 fallback 或上层兜底。

不在这里导入 ``agent.config``，避免测试/工具导入时触发 Redis 初始化。
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware import (
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
)
from langchain_core.exceptions import ModelError

DEFAULT_RETRY_BACKOFF_FACTOR = 2.0
DEFAULT_RETRY_JITTER = True

_RETRYABLE_NAME_MARKERS = (
    "timeout",
    "ratelimit",
    "rate_limit",
    "connection",
    "connecterror",
    "internalserver",
    "serviceunavailable",
    "overloaded",
    "temporarily",
)
_RETRYABLE_TEXT_MARKERS = (
    "timed out",
    "timeout",
    "rate limit",
    "connection reset",
    "connection error",
    "temporarily unavailable",
    "server overloaded",
    "service unavailable",
    "429",
)


def is_retryable_model_error(exc: BaseException) -> bool:
    """判断模型调用异常是否值得重试。

    优先使用异常自带的状态码/SDK 可重试标记；无法判断时只按保守关键词
    匹配，避免对 400/401 这类不可恢复错误做无意义重试。
    """
    if not isinstance(exc, Exception):
        return False

    if isinstance(exc, ModelError):
        return bool(getattr(exc, "is_retryable", False))

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in {408, 409, 425, 429}:
            return True
        return 500 <= status < 600

    name = type(exc).__name__.casefold()
    text = str(exc).casefold()
    if any(marker in name for marker in _RETRYABLE_NAME_MARKERS):
        return True
    if any(marker in text for marker in _RETRYABLE_TEXT_MARKERS):
        return True
    return False


def build_model_retry_middleware(
    *,
    max_retries: int,
    initial_delay: float,
    max_delay: float,
    backoff_factor: float = DEFAULT_RETRY_BACKOFF_FACTOR,
    jitter: bool = DEFAULT_RETRY_JITTER,
) -> ModelRetryMiddleware:
    """构建带可重试分类的模型重试中间件。

    ``on_failure="error"`` 是刻意的：重试耗尽后抛出最后一次异常，让外层的
    ``ModelFallbackMiddleware`` 接管，而不是返回一条「模型失败」的普通消息。
    """
    return ModelRetryMiddleware(
        max_retries=max(0, int(max_retries)),
        retry_on=is_retryable_model_error,
        on_failure="error",
        backoff_factor=max(0.0, float(backoff_factor)),
        initial_delay=max(0.0, float(initial_delay)),
        max_delay=max(0.0, float(max_delay)),
        jitter=bool(jitter),
    )


def build_model_fallback_middleware(
    fallback_model: Any,
) -> ModelFallbackMiddleware:
    """构建主模型失败后的备用模型中间件。"""
    return ModelFallbackMiddleware(fallback_model)


__all__ = [
    "is_retryable_model_error",
    "build_model_retry_middleware",
    "build_model_fallback_middleware",
]
