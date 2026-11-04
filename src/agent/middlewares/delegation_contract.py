"""委派契约中间件：给 deepagents 的 `task` 工具加一层结构化护栏。

为什么需要它：
`task` 的入参只有一个自由文本 `description`，子 Agent 默认不继承对话上下文
（`SubAgent.mode` 未设为 fork），所以「按模板写」一旦没做到，子 Agent 就是
真的不知道用户偏好和输出要求——提示词层的约定没有任何代码兜底。

本中间件的策略是分级的，不做成一刀切硬失败：

- 下行：`【任务目标】`/`【需求正文】` 缺失或为空 → 拦回给模型，返回可修复的
  tool error（不派发子 Agent，但模型能立刻补写重试）；
  `【用户偏好】`/`【输出要求】` 缺失 → 用运行时身份和固定输出形态补齐，
  记录 `delegation.brief_patched`，不打断主流程。
- 上行：子 Agent 文本按契约解析；缺段 → `status=degraded` 信封，显式提示
  主 Agent 去读文件产物，而不是把残缺文本当结论；解析成功 → 规范信封。
"""
from __future__ import annotations

from typing import Any, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.log_utils import MyLogger
from agent.subagents.contract import (
    BRIEF_REQUIRED_SECTIONS,
    REPORT_CONTRACTS,
    ReportContract,
    build_default_sections,
    parse_report,
    patch_brief,
    render_brief_repair_hint,
    render_envelope,
    validate_brief,
)

TASK_TOOL_NAME = "task"


class DelegationContractMiddleware(AgentMiddleware):
    """给 `task` 补下行简报校验与上行回传规范化。"""

    def __init__(
        self,
        *,
        hard_sections: tuple[str, ...] = BRIEF_REQUIRED_SECTIONS,
        contracts: Mapping[str, ReportContract] | None = None,
        logger: Any = None,
    ) -> None:
        super().__init__()
        self._hard_sections = tuple(hard_sections)
        self._contracts = dict(contracts or REPORT_CONTRACTS)
        self._logger = logger or MyLogger().get_logger()

    # -- 下行 ---------------------------------------------------------------
    def _prepare(
            self, request: ToolCallRequest
    ) -> tuple[ToolCallRequest, ToolMessage | None]:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return request, None

        args = dict(request.tool_call.get("args") or {})
        description = args.get("description")
        if not isinstance(description, str) or not description.strip():
            # 缺 description 属于 schema 错误，交给框架报，避免双重拦截。
            return request, None

        check = validate_brief(description)
        blocking = tuple(s for s in self._hard_sections if s in check.blocking)
        if blocking:
            self._logger.warning(
                "delegation.brief_rejected subagent=%s missing=%s",
                args.get("subagent_type"),
                ",".join(blocking),
            )
            return request, self._error_message(
                request, render_brief_repair_hint(check, blocking)
            )

        context = getattr(getattr(request, "runtime", None), "context", None)
        subagent_type = str(args.get("subagent_type") or "")
        patched = patch_brief(
            description, build_default_sections(subagent_type, context)
        )
        if patched != description:
            # 只回写 description，subagent_type 等字段保持模型原值。
            new_call = {**request.tool_call, "args": {**args, "description": patched}}
            request = request.override(tool_call=new_call)
            self._logger.info(
                "delegation.brief_patched subagent=%s filled=%s",
                subagent_type,
                ",".join(sorted(set(validate_brief(description).blocking))),
            )
        return request, None

    # -- 上行 ---------------------------------------------------------------
    def _finalize(self, request: ToolCallRequest, result: Any) -> Any:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return result
        if not isinstance(result, ToolMessage):
            # Command / interrupt 等直接透传。
            return result
        if getattr(result, "status", "success") != "success":
            return result

        agent = str((request.tool_call.get("args") or {}).get("subagent_type") or "")
        envelope = parse_report(
            result.content if isinstance(result.content, str) else str(result.content),
            agent,
        )
        if not envelope.ok:
            self._logger.warning(
                "delegation.report_degraded agent=%s missing=%s path=%s",
                agent,
                ",".join(envelope.missing),
                envelope.report_path,
            )
        return ToolMessage(
            content=render_envelope(envelope),
            tool_call_id=result.tool_call_id,
            name=result.name or TASK_TOOL_NAME,
            status=getattr(result, "status", "success"),
        )

    def _error_message(self, request: ToolCallRequest, text: str) -> ToolMessage:
        return ToolMessage(
            content=text,
            tool_call_id=request.tool_call.get("id") or "",
            name=TASK_TOOL_NAME,
            status="error",
        )

    # -- hooks --------------------------------------------------------------
    def wrap_tool_call(self, request, handler):
        prepared, error = self._prepare(request)
        if error is not None:
            return error
        return self._finalize(prepared, handler(prepared))

    async def awrap_tool_call(self, request, handler):
        prepared, error = self._prepare(request)
        if error is not None:
            return error
        return self._finalize(prepared, await handler(prepared))


__all__ = ["DelegationContractMiddleware", "TASK_TOOL_NAME"]
