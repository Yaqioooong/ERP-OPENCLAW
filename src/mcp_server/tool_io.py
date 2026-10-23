from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastmcp import Context

from mcp_server.contracts import (
    ToolError,
    ToolErrorCode,
    ToolMeta,
    ToolResponse,
)

logger = logging.getLogger(__name__)


def _request_id(ctx: Context) -> str:
    try:
        request = ctx.request_context.request
        headers = getattr(request, "headers", None) or {}
        value = headers.get("x-request-id") if hasattr(headers, "get") else None
        if value:
            return str(value)
    except Exception:
        pass
    return f"req-{uuid.uuid4().hex[:16]}"


def _meta(
        *,
        tool: str,
        ctx: Context,
        started: float,
        upstream_code: int | None = None,
) -> ToolMeta:
    return ToolMeta(
        tool=tool,
        request_id=_request_id(ctx),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        upstream_code=upstream_code,
    )


def success(
        data: Any,
        *,
        tool: str,
        ctx: Context,
        started: float,
        upstream_code: int | None = 200,
) -> ToolResponse:
    return ToolResponse(
        ok=True,
        data=data,
        error=None,
        meta=_meta(
            tool=tool,
            ctx=ctx,
            started=started,
            upstream_code=upstream_code,
        ),
    )


def failure(
        *,
        code: ToolErrorCode,
        message: str,
        tool: str,
        ctx: Context,
        started: float,
        retryable: bool = False,
        details: dict | None = None,
        upstream_code: int | None = None,
) -> ToolResponse:
    return ToolResponse(
        ok=False,
        data=None,
        error=ToolError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
        meta=_meta(
            tool=tool,
            ctx=ctx,
            started=started,
            upstream_code=upstream_code,
        ),
    )


def _error_from_status(
        *,
        status: int,
        message: str,
        tool: str,
        ctx: Context,
        started: float,
        details: dict | None = None,
) -> ToolResponse:
    if status == 400 or status == 422:
        code = ToolErrorCode.INVALID_ARGUMENT
        retryable = False
    elif status == 401:
        code = ToolErrorCode.UNAUTHENTICATED
        retryable = False
    elif status == 403:
        code = ToolErrorCode.FORBIDDEN
        retryable = False
    elif status == 404:
        code = ToolErrorCode.NOT_FOUND
        retryable = False
    elif status == 409:
        code = ToolErrorCode.CONFLICT
        retryable = False
    elif status == 429:
        code = ToolErrorCode.RATE_LIMITED
        retryable = True
    elif status >= 500:
        code = ToolErrorCode.UPSTREAM_UNAVAILABLE
        retryable = True
    else:
        code = ToolErrorCode.INTERNAL_ERROR
        retryable = False
    return failure(
        code=code,
        message=message,
        tool=tool,
        ctx=ctx,
        started=started,
        retryable=retryable,
        details=details,
        upstream_code=status,
    )


def _business_error(
        *,
        body: dict,
        tool: str,
        ctx: Context,
        started: float,
        upstream_code: int,
) -> ToolResponse:
    code_value = str(body.get("code") or "")
    text = str(body.get("message") or body)

    duplicate = (
            code_value.upper() in {"ORDER_DUPLICATE", "DUPLICATE"}
            or "已存在" in text
            or "重复" in text
            or "duplicate" in text.lower()
    )
    numeric_code = None
    try:
        numeric_code = int(code_value)
    except (TypeError, ValueError):
        pass

    if duplicate:
        error_code = ToolErrorCode.CONFLICT
        retryable = False
    elif code_value.upper() in {"NOT_FOUND", "602"}:
        error_code = ToolErrorCode.NOT_FOUND
        retryable = False
    elif code_value.upper() in {"UNAUTHORIZED", "UNAUTHENTICATED"}:
        error_code = ToolErrorCode.UNAUTHENTICATED
        retryable = False
    elif code_value.upper() in {"FORBIDDEN", "NO_PERMISSION"}:
        error_code = ToolErrorCode.FORBIDDEN
        retryable = False
    elif numeric_code == 429:
        error_code = ToolErrorCode.RATE_LIMITED
        retryable = True
    elif numeric_code is not None and numeric_code >= 500:
        error_code = ToolErrorCode.UPSTREAM_UNAVAILABLE
        retryable = True
    else:
        error_code = ToolErrorCode.BUSINESS_REJECTED
        retryable = False

    return failure(
        code=error_code,
        message=text,
        tool=tool,
        ctx=ctx,
        started=started,
        retryable=retryable,
        details={
            "upstream_code": body.get("code"),
            "duplicate": duplicate,
        },
        upstream_code=upstream_code,
    )


async def request_json(
        *,
        ctx: Context,
        tool: str,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
        not_found_ok: bool = False,
) -> tuple[Any | None, ToolResponse | None]:
    """返回 (data, error)。

    error 为 None 表示成功；data 可能为 None（确认不存在或 204）。

    错误分层：
    - 超时 -> TIMEOUT，可重试
    - 连接/传输失败 -> UPSTREAM_UNAVAILABLE，可重试
    - HTTP 状态码 -> 按 4xx/5xx 映射
    - 响应不是合法 JSON -> UPSTREAM_UNAVAILABLE
    - 代码/配置错误（非法 URL、TypeError 等）-> INTERNAL_ERROR，不伪装成上游故障
    """
    started = time.monotonic()
    lifespan_context = getattr(
        ctx.request_context, "lifespan_context", None
    ) or {}
    client: httpx.AsyncClient | None = (
        lifespan_context.get("http_client")
        if hasattr(lifespan_context, "get")
        else None
    )
    if client is None:
        return None, failure(
            code=ToolErrorCode.UPSTREAM_UNAVAILABLE,
            message="MCP HTTP client 未初始化",
            tool=tool,
            ctx=ctx,
            started=started,
            retryable=True,
            details={"reason": "HTTP_CLIENT_NOT_INITIALIZED"},
        )

    try:
        response = await client.request(
            method,
            path,
            json=json_body,
            params=params,
        )
        if response.status_code == 404 and not_found_ok:
            return None, None

        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None, None

        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            logger.warning(
                "MCP tool %s 返回非 JSON: status=%s error=%s",
                tool,
                response.status_code,
                exc,
            )
            return None, failure(
                code=ToolErrorCode.UPSTREAM_UNAVAILABLE,
                message="ERP 返回了无法解析的响应体",
                tool=tool,
                ctx=ctx,
                started=started,
                retryable=True,
                details={
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "reason": "INVALID_JSON",
                },
                upstream_code=response.status_code,
            )

        if not isinstance(body, dict):
            return body, None

        upstream_code = body.get("code")
        if (
            not_found_ok
            and upstream_code is not None
            and str(upstream_code) == "602"
        ):
            return None, None
        if upstream_code is not None and str(upstream_code) != "200":
            return None, _business_error(
                body=body,
                tool=tool,
                ctx=ctx,
                started=started,
                upstream_code=response.status_code,
            )
        return body.get("data"), None

    except httpx.TimeoutException as exc:
        logger.warning(
            "MCP tool %s 超时: method=%s path=%s error=%s",
            tool, method, path, exc,
        )
        return None, failure(
            code=ToolErrorCode.TIMEOUT,
            message=f"ERP 请求超时：{exc}",
            tool=tool,
            ctx=ctx,
            started=started,
            retryable=True,
            details={
                "method": method,
                "path": path,
                "exception_type": type(exc).__name__,
            },
        )
    except httpx.HTTPStatusError as exc:
        body: dict = {}
        try:
            parsed = exc.response.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            # 错误响应体解析失败不能覆盖原始 HTTP 状态。
            body = {}
        logger.info(
            "MCP tool %s HTTP 错误: status=%s path=%s",
            tool, exc.response.status_code, path,
        )
        return None, _error_from_status(
            status=exc.response.status_code,
            message=str(body.get("message") or exc.response.text or exc),
            tool=tool,
            ctx=ctx,
            started=started,
            details={"body": body} if body else {},
        )
    except httpx.RequestError as exc:
        logger.warning(
            "MCP tool %s 传输失败: method=%s path=%s error=%s",
            tool, method, path, exc,
        )
        return None, failure(
            code=ToolErrorCode.UPSTREAM_UNAVAILABLE,
            message=f"ERP 连接失败：{exc}",
            tool=tool,
            ctx=ctx,
            started=started,
            retryable=True,
            details={
                "method": method,
                "path": path,
                "exception_type": type(exc).__name__,
            },
        )
    except (httpx.InvalidURL, ValueError, TypeError) as exc:
        # 这些通常是路径/参数/客户端构造错误，不能当成“上游暂时不可用”。
        logger.exception(
            "MCP tool %s 请求配置错误: method=%s path=%s",
            tool, method, path,
        )
        return None, failure(
            code=ToolErrorCode.INTERNAL_ERROR,
            message=f"工具请求配置错误：{exc}",
            tool=tool,
            ctx=ctx,
            started=started,
            retryable=False,
            details={
                "method": method,
                "path": path,
                "exception_type": type(exc).__name__,
            },
        )
    except Exception as exc:
        # 兜底：保留工具契约，但记录完整堆栈，避免把 bug 伪装成上游故障。
        logger.exception(
            "MCP tool %s 未预期异常: method=%s path=%s",
            tool, method, path,
        )
        return None, failure(
            code=ToolErrorCode.INTERNAL_ERROR,
            message="工具内部错误，请查看服务端日志",
            tool=tool,
            ctx=ctx,
            started=started,
            retryable=False,
            details={
                "method": method,
                "path": path,
                "exception_type": type(exc).__name__,
            },
        )
