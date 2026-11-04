"""委派契约中间件：给 deepagents 的 `task` 工具加一层结构化护栏。

为什么需要它：
`task` 的入参只有一个自由文本 `description`，子 Agent 默认不继承对话上下文，
所以「按模板写」一旦没做到，子 Agent 就真的不知道用户偏好和输出要求。

本中间件分三层兜底：

- 下行：必需段缺失 → 拦回给模型，返回可修复的 tool error（不派发）；
  可补齐段缺失 → 用可信运行时上下文补齐；未知子 Agent → 派发前拦截。
- 上行：把子 Agent 文本解析成版本化 JSON 信封（ok/degraded/error/timeout），
  缺段/空返回不再被当成成功。
- 运行时：子 Agent 异常/超时不再冒泡打断主回合；中断与取消原样传播；
  订单写类失败标记 ``side_effect_unknown``，不自动重试。

当前 deepagents 的 `task` 成功时返回 ``Command(update={"messages": [...]})``，
不是直接的 ``ToolMessage``，所以上行处理必须同时兼容两种返回形态。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.log_utils import MyLogger
from agent.subagents.contract import (
    BRIEF_REQUIRED_SECTIONS,
    CODE_AGENT_UNKNOWN,
    CODE_BRIEF_INVALID,
    DEFAULT_RAW_TEXT_LIMIT,
    REPORT_CONTRACTS,
    ReportContract,
    BriefValidationError,
    build_error_result,
    build_exception_result,
    build_result_from_text,
    build_timeout_result,
    prepare_brief,
    render_brief_repair_hint,
    render_result,
    validate_brief,
)

TASK_TOOL_NAME = "task"

#: 默认超时；调用方可按子 Agent 覆盖。
DEFAULT_AGENT_TIMEOUT_SECONDS = 600.0
DEFAULT_TIMEOUT_BY_AGENT: dict[str, float] = {
    "procurement-analyst": 600.0,
    "procurement-order": 120.0,
}


class DelegationContractMiddleware(AgentMiddleware):
    """给 `task` 补下行简报校验与上行结构化回传。"""

    def __init__(
        self,
        *,
        hard_sections: tuple[str, ...] = BRIEF_REQUIRED_SECTIONS,
        contracts: Mapping[str, ReportContract] | None = None,
        known_subagents: tuple[str, ...] | list[str] | None = None,
        timeout_by_agent: Mapping[str, float] | None = None,
        default_timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS,
        raw_text_limit: int = DEFAULT_RAW_TEXT_LIMIT,
        logger: Any = None,
    ) -> None:
        super().__init__()
        self._hard_sections = tuple(hard_sections)
        self._contracts = dict(contracts or REPORT_CONTRACTS)
        self._known_subagents = (
            tuple(sorted(set(known_subagents)))
            if known_subagents
            else None
        )
        self._timeouts = {
            **DEFAULT_TIMEOUT_BY_AGENT,
            **dict(timeout_by_agent or {}),
        }
        self._default_timeout = float(default_timeout_seconds)
        self._raw_text_limit = int(raw_text_limit)
        self._logger = logger or MyLogger().get_logger()

    # -- 下行 ---------------------------------------------------------------
    def _prepare(
            self, request: ToolCallRequest
    ) -> tuple[ToolCallRequest, ToolMessage | None]:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return request, None

        args = dict(request.tool_call.get("args") or {})
        subagent_type = str(args.get("subagent_type") or "").strip()
        if self._known_subagents is not None and (
            not subagent_type or subagent_type not in self._known_subagents
        ):
            self._logger.warning(
                "delegation.agent_unknown subagent=%s available=%s",
                subagent_type or "-",
                ",".join(self._known_subagents),
            )
            result = build_error_result(
                agent=subagent_type or "unknown",
                code=CODE_AGENT_UNKNOWN,
                message=(
                    f"未知子 Agent 类型：{subagent_type or '（空）'}。"
                    f"可用类型：{'、'.join(self._known_subagents)}。"
                ),
                retryable=True,
            )
            return request, self._failure_message(request, result)

        description = args.get("description")
        if not isinstance(description, str) or not description.strip():
            # 缺 description 属于 schema 错误，交给框架报，避免双重拦截。
            return request, None

        context = getattr(getattr(request, "runtime", None), "context", None)
        try:
            patched = prepare_brief(
                description,
                subagent_type,
                context=context,
                required_sections=self._hard_sections,
            )
        except BriefValidationError as exc:
            check = validate_brief(description)
            self._logger.warning(
                "delegation.brief_rejected subagent=%s missing=%s",
                subagent_type,
                ",".join(exc.missing),
            )
            hint = render_brief_repair_hint(check, exc.missing)
            result = build_error_result(
                agent=subagent_type or "unknown",
                code=CODE_BRIEF_INVALID,
                message="委派简报缺少必需段落，未派发子 Agent。",
                retryable=True,
                missing=exc.missing,
            )
            return request, self._failure_message(request, result, extra=hint)

        if patched != description:
            new_call = {
                **request.tool_call,
                "args": {**args, "description": patched},
            }
            request = request.override(tool_call=new_call)
            self._logger.info(
                "delegation.brief_patched subagent=%s filled=%s",
                subagent_type,
                ",".join(sorted(set(validate_brief(description).blocking))),
            )
        return request, None

    # -- 上行 ---------------------------------------------------------------
    def _finalize(
        self,
        request: ToolCallRequest,
        result: Any,
        *,
        started_at: float,
    ) -> Any:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return result

        agent = self._subagent_type(request)
        duration_ms = self._duration_ms(started_at)

        if isinstance(result, Command):
            message = self._find_tool_message(
                result, request.tool_call.get("id")
            )
            if message is None:
                # 中断/仅 state update 的 Command：原样透传。
                return result
            if self._is_existing_error(message):
                return result
            envelope = build_result_from_text(
                self._content_text(message.content),
                agent,
                duration_ms=duration_ms,
                raw_text_limit=self._raw_text_limit,
            )
            self._log_result(agent, envelope)
            new_message = self._replace_message(message, envelope)
            update = result.update if isinstance(result.update, dict) else {}
            messages = update.get("messages")
            if not isinstance(messages, list):
                return result
            replaced = [
                new_message
                if isinstance(item, ToolMessage)
                and item.tool_call_id == message.tool_call_id
                else item
                for item in messages
            ]
            return replace(result, update={**update, "messages": replaced})

        if isinstance(result, ToolMessage):
            if self._is_existing_error(result):
                return result
            envelope = build_result_from_text(
                self._content_text(result.content),
                agent,
                duration_ms=duration_ms,
                raw_text_limit=self._raw_text_limit,
            )
            self._log_result(agent, envelope)
            return self._replace_message(result, envelope)

        if isinstance(result, str):
            envelope = build_result_from_text(
                result,
                agent,
                duration_ms=duration_ms,
                raw_text_limit=self._raw_text_limit,
            )
            self._log_result(agent, envelope)
            return ToolMessage(
                content=render_result(envelope),
                tool_call_id=request.tool_call.get("id") or "",
                name=TASK_TOOL_NAME,
                status=(
                    "error"
                    if envelope.status in {"error", "timeout"}
                    else "success"
                ),
            )

        return result

    # -- hooks --------------------------------------------------------------
    def wrap_tool_call(self, request, handler):
        prepared, error = self._prepare(request)
        if error is not None:
            return error
        started_at = time.monotonic()
        try:
            result = handler(prepared)
        except GraphInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - 契约层必须兜底
            agent = self._subagent_type(prepared)
            self._logger.warning(
                "delegation.exception subagent=%s error_type=%s",
                agent,
                type(exc).__name__,
            )
            return self._failure_message(
                prepared,
                build_exception_result(
                    exc,
                    agent=agent,
                    duration_ms=self._duration_ms(started_at),
                ),
            )
        return self._finalize(prepared, result, started_at=started_at)

    async def awrap_tool_call(self, request, handler):
        prepared, error = self._prepare(request)
        if error is not None:
            return error

        agent = self._subagent_type(prepared)
        timeout_seconds = self._timeout_for(agent)
        started_at = time.monotonic()
        try:
            if timeout_seconds > 0:
                result = await asyncio.wait_for(
                    handler(prepared), timeout=timeout_seconds
                )
            else:
                result = await handler(prepared)
        except (GraphInterrupt, asyncio.CancelledError):
            raise
        except asyncio.TimeoutError:
            self._logger.warning(
                "delegation.timeout subagent=%s timeout_s=%s",
                agent,
                timeout_seconds,
            )
            return self._failure_message(
                prepared,
                build_timeout_result(
                    agent=agent,
                    timeout_seconds=timeout_seconds,
                    duration_ms=self._duration_ms(started_at),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 契约层必须兜底
            self._logger.warning(
                "delegation.exception subagent=%s error_type=%s",
                agent,
                type(exc).__name__,
            )
            return self._failure_message(
                prepared,
                build_exception_result(
                    exc,
                    agent=agent,
                    duration_ms=self._duration_ms(started_at),
                ),
            )
        return self._finalize(prepared, result, started_at=started_at)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _subagent_type(request: ToolCallRequest) -> str:
        return str(
            (request.tool_call.get("args") or {}).get("subagent_type") or ""
        ) or "unknown"

    def _timeout_for(self, agent: str) -> float:
        return float(self._timeouts.get(agent, self._default_timeout))

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    elif "text" in item:
                        parts.append(str(item["text"]))
            return "".join(parts)
        return str(content or "")

    @staticmethod
    def _is_existing_error(message: ToolMessage) -> bool:
        # 框架已经生成的 error ToolMessage（如参数校验失败）原样保留，
        # 避免把可读的原始错误包成第二层信封。
        return getattr(message, "status", "success") == "error"

    @staticmethod
    def _find_tool_message(
        result: Command,
        tool_call_id: str | None,
    ) -> ToolMessage | None:
        update = result.update
        if not isinstance(update, dict):
            return None
        messages = update.get("messages")
        if not isinstance(messages, list):
            return None
        candidates = [
            message
            for message in messages
            if isinstance(message, ToolMessage)
        ]
        if not candidates:
            return None
        if tool_call_id:
            for message in reversed(candidates):
                if message.tool_call_id == tool_call_id:
                    return message
        return candidates[-1]

    def _replace_message(
        self,
        original: ToolMessage,
        envelope,
    ) -> ToolMessage:
        return ToolMessage(
            content=render_result(envelope),
            tool_call_id=original.tool_call_id,
            name=original.name or TASK_TOOL_NAME,
            status=(
                "error"
                if envelope.status in {"error", "timeout"}
                else "success"
            ),
        )

    def _failure_message(
        self,
        request: ToolCallRequest,
        envelope,
        *,
        extra: str = "",
    ) -> ToolMessage:
        content = render_result(envelope)
        if extra:
            content = f"{extra}\n\n{content}"
        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call.get("id") or "",
            name=TASK_TOOL_NAME,
            status="error",
        )

    def _log_result(self, agent: str, envelope) -> None:
        self._logger.info(
            "delegation.result subagent=%s status=%s code=%s "
            "retryable=%s side_effect_unknown=%s duration_ms=%s "
            "has_report_path=%s",
            agent,
            envelope.status,
            envelope.code or "-",
            str(envelope.retryable).lower(),
            str(envelope.side_effect_unknown).lower(),
            envelope.duration_ms or 0,
            str(bool(envelope.report_path)).lower(),
        )
        if envelope.status == "degraded":
            self._logger.warning(
                "delegation.report_degraded agent=%s missing=%s path=%s",
                agent,
                ",".join(envelope.missing),
                envelope.report_path,
            )


__all__ = [
    "DelegationContractMiddleware",
    "TASK_TOOL_NAME",
    "DEFAULT_TIMEOUT_BY_AGENT",
]
