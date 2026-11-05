"""工具调用异常与超时兜底中间件。

LangGraph ``ToolNode`` 默认只把 ``ToolInvocationError`` 转成 tool error，其余
异常会重新抛出。对 MCP/HTTP/沙箱这类外部工具来说，一次超时或连接断开就可能
打断整个 Agent 回合。

本中间件在 ``wrap_tool_call`` / ``awrap_tool_call`` 边界做三件事：

1. 超时：按工具名设置上限，异步路径用 ``asyncio.wait_for``；
2. 重试：只读工具遇到超时/连接/429 等瞬时错误时最多重试一次；
3. 兜底：写工具不自动重试，返回带 ``side_effect_unknown`` 的结构化
   ``ToolMessage``；写操作可能已执行时提示先查询确认。

``GraphInterrupt`` 与 ``CancelledError`` 原样传播，``task`` 默认跳过（由
``DelegationContractMiddleware`` 处理）。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.log_utils import MyLogger

DEFAULT_TOOL_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_SECONDS = 0.25

#: 特殊工具的超时；未列出的走默认值。
TIMEOUT_BY_TOOL: dict[str, float] = {
    "execute": 300.0,
    "generate_visualization": 120.0,
    "generate_spreadsheet": 120.0,
    "web_search": 60.0,
    "compact_conversation": 120.0,
    "assign_skill": 120.0,
    "download_sandbox_file": 120.0,
    "start_procurement_task": 60.0,
    "order_create": 120.0,
    "order_update": 120.0,
}

#: 写工具：超时/异常不自动重试，并标记 side_effect_unknown。
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "order_create",
        "order_update",
        "order_cancel",
        "execute",
        "write_file",
        "edit_file",
        "write_todos",
        "assign_skill",
        "save_user_preferences",
        "download_sandbox_file",
        "start_procurement_task",
        "compact_conversation",
        "generate_spreadsheet",
    }
)

TOOL_TIMEOUT = "TOOL_TIMEOUT"
TOOL_RATE_LIMITED = "TOOL_RATE_LIMITED"
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
TOOL_FAILED = "TOOL_FAILED"

_TRANSIENT_CODES = {TOOL_TIMEOUT, TOOL_RATE_LIMITED, TOOL_UNAVAILABLE}


class ToolResilienceMiddleware(AgentMiddleware):
    """给工具调用加超时、有限重试和结构化错误兜底。"""

    def __init__(
        self,
        *,
        timeout_by_tool: Mapping[str, float] | None = None,
        default_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        skip_tools: tuple[str, ...] | list[str] = ("task",),
        logger: Any = None,
    ) -> None:
        super().__init__()
        self._timeouts = {
            **TIMEOUT_BY_TOOL,
            **dict(timeout_by_tool or {}),
        }
        self._default_timeout = float(default_timeout_seconds)
        self._max_attempts = max(1, int(max_attempts))
        self._retry_delay = max(0.0, float(retry_delay_seconds))
        self._skip_tools = frozenset(skip_tools)
        self._logger = logger or MyLogger().get_logger()

    # -- hooks --------------------------------------------------------------
    def wrap_tool_call(self, request, handler):
        name = self._tool_name(request)
        if name in self._skip_tools:
            return handler(request)

        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 - 工具边界必须兜底
            status, code = self._classify(exc)
            return self._failure_message(
                request,
                status=status,
                code=code,
                attempts=1,
                is_write=self._is_write_tool(name),
                duration_ms=0,
            )

    async def awrap_tool_call(self, request, handler):
        name = self._tool_name(request)
        if name in self._skip_tools:
            return await handler(request)

        timeout = self._timeout_for(name)
        is_write = self._is_write_tool(name)
        max_attempts = 1 if is_write else self._max_attempts
        started_at = time.monotonic()
        last_exc: BaseException | None = None
        last_status = "error"
        last_code = TOOL_FAILED
        attempts_used = 0

        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            try:
                if timeout > 0:
                    return await asyncio.wait_for(
                        handler(request), timeout=timeout
                    )
                return await handler(request)
            except (GraphBubbleUp, asyncio.CancelledError):
                raise
            except asyncio.TimeoutError as exc:
                last_exc = exc
                last_status = "timeout"
                last_code = TOOL_TIMEOUT
                if attempt >= max_attempts:
                    break
                self._log_retry(name, attempt, last_code)
                await asyncio.sleep(self._retry_delay * attempt)
            except Exception as exc:  # noqa: BLE001 - 工具边界必须兜底
                last_exc = exc
                last_status, last_code = self._classify(exc)
                retryable = (
                    not is_write and last_code in _TRANSIENT_CODES
                )
                if attempt >= max_attempts or not retryable:
                    break
                self._log_retry(name, attempt, last_code)
                await asyncio.sleep(self._retry_delay * attempt)

        self._logger.warning(
            "tool.call.failed tool=%s status=%s code=%s attempts=%s error_type=%s",
            name,
            last_status,
            last_code,
            attempts_used,
            type(last_exc).__name__ if last_exc is not None else "-",
        )
        return self._failure_message(
            request,
            status=last_status,
            code=last_code,
            attempts=attempts_used,
            is_write=is_write,
            duration_ms=self._duration_ms(started_at),
        )

    # -- policy -------------------------------------------------------------
    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name") or "unknown")

    def _timeout_for(self, name: str) -> float:
        return float(self._timeouts.get(name, self._default_timeout))

    @staticmethod
    def _is_write_tool(name: str) -> bool:
        if name in WRITE_TOOLS:
            return True
        return name.startswith(
            ("order_create", "order_update", "order_cancel")
        )

    @staticmethod
    def _classify(exc: BaseException) -> tuple[str, str]:
        name = type(exc).__name__.casefold()
        text = str(exc).casefold()
        if "timeout" in name or "timed out" in text or "timeouterror" in name:
            return "timeout", TOOL_TIMEOUT
        if "ratelimit" in name or "rate_limit" in name or "429" in text:
            return "error", TOOL_RATE_LIMITED
        if any(
            token in name
            for token in (
                "connection",
                "transport",
                "connecterror",
                "network",
            )
        ) or "connection" in text:
            return "error", TOOL_UNAVAILABLE
        return "error", TOOL_FAILED

    # -- rendering ----------------------------------------------------------
    def _failure_message(
        self,
        request: ToolCallRequest,
        *,
        status: str,
        code: str,
        attempts: int,
        is_write: bool,
        duration_ms: int,
    ) -> ToolMessage:
        name = self._tool_name(request)
        side_effect_unknown = is_write
        retryable = (
            not is_write and code in _TRANSIENT_CODES
        )
        message = (
            f"工具 {name} 调用失败：{code}。"
            if not side_effect_unknown
            else (
                f"工具 {name} 调用失败：{code}，写操作可能已执行，"
                "结果未知。"
            )
        )
        payload = {
            "status": status,
            "tool": name,
            "code": code,
            "message": message,
            "retryable": retryable,
            "side_effect_unknown": side_effect_unknown,
            "attempts": attempts,
            "duration_ms": duration_ms,
        }
        guidance = self._guidance(
            is_write=is_write,
            retryable=retryable,
        )
        content = "\n".join(
            [
                (
                    f"【工具调用失败】tool={name} status={status} "
                    f"code={code} retryable={str(retryable).lower()} "
                    f"side_effect_unknown={str(side_effect_unknown).lower()}"
                ),
                "```json",
                json.dumps(payload, ensure_ascii=False, indent=2),
                "```",
                guidance,
            ]
        )
        self._logger.info(
            "tool.call.result tool=%s status=%s code=%s retryable=%s "
            "side_effect_unknown=%s attempts=%s duration_ms=%s",
            name,
            status,
            code,
            str(retryable).lower(),
            str(side_effect_unknown).lower(),
            attempts,
            duration_ms,
        )
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call.get("id") or "",
            name=name,
            status="error",
        )

    @staticmethod
    def _guidance(*, is_write: bool, retryable: bool) -> str:
        if is_write:
            return (
                "该工具可能已执行：不要自动重试。订单请先用 "
                "order_search_details 查询实际状态；其他写操作请先检查"
                "文件/命令/资源状态，确认未执行后再决定。"
            )
        if retryable:
            return (
                "该失败标记为可重试；可以换一种查询、缩小范围后重试，"
                "或向用户说明部分数据暂不可用。"
            )
        return (
            "该失败按不可重试处理；请改派、拆任务或向用户说明限制。"
        )

    def _log_retry(self, name: str, attempt: int, code: str) -> None:
        self._logger.warning(
            "tool.call.retry tool=%s attempt=%s code=%s",
            name,
            attempt,
            code,
        )

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))


__all__ = [
    "ToolResilienceMiddleware",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETRY_DELAY_SECONDS",
    "TIMEOUT_BY_TOOL",
    "WRITE_TOOLS",
    "TOOL_TIMEOUT",
    "TOOL_RATE_LIMITED",
    "TOOL_UNAVAILABLE",
    "TOOL_FAILED",
]
