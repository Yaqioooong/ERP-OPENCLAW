"""委派契约的运行时接入测试（规格 specs/010 的 AC-14）。

不直接调用中间件方法，而是通过真实的 LangGraph `ToolNode` 执行 `task` 调用，
覆盖当前框架返回 `Command` 的形态，避免“纯函数测试绿、运行时没接上”。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.prebuilt.tool_node import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.middlewares.delegation_contract import DelegationContractMiddleware

CONTRACT_TEXT = (
    "【报告路径】/analysis/runtime.md\n"
    "【分析内容摘要】运行时链路\n"
    "【分析结论】已接入\n"
    "【采购建议】保持\n"
)

FULL_BRIEF = (
    "【任务目标】验证运行时\n"
    "【用户偏好】表格\n"
    "【需求正文】验证\n"
    "【输出要求】按契约返回\n"
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def info(self, msg: str, *args) -> None:
        self.events.append(("info", msg % args if args else msg))

    def warning(self, msg: str, *args) -> None:
        self.events.append(("warning", msg % args if args else msg))


class _TaskArgs(BaseModel):
    description: str = Field(..., description="委派简报")
    subagent_type: str = Field(..., description="子 Agent 类型")


def _tool(message: ToolMessage) -> StructuredTool:
    async def fake_task(description: str, subagent_type: str) -> Command:
        return Command(update={"messages": [message]})

    return StructuredTool.from_function(
        coroutine=fake_task,
        name="task",
        description="fake task tool",
        args_schema=_TaskArgs,
    )


def _state() -> dict:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": FULL_BRIEF,
                            "subagent_type": "procurement-analyst",
                        },
                        "id": "call-1",
                    }
                ],
            )
        ]
    }


def _config() -> dict:
    context = SimpleNamespace(user_id="u-1", username="张三", roles=("buyer",))
    return {
        CONF: {
            CONFIG_KEY_RUNTIME: Runtime(context=context, store=None),
        }
    }


def _messages(result) -> list:
    if isinstance(result, list):
        result = result[0] if result else None
    if isinstance(result, Command):
        return list((result.update or {}).get("messages") or [])
    if isinstance(result, dict):
        return list(result.get("messages") or [])
    if isinstance(result, ToolMessage):
        return [result]
    return []


def _envelope(content: str) -> dict:
    block = content.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(block)


@pytest.mark.anyio
async def test_toolnode_command_result_gets_structured_envelope():
    middleware = DelegationContractMiddleware(
        known_subagents=("procurement-analyst",),
        logger=_Logger(),
    )
    node = ToolNode(
        [_tool(ToolMessage(content=CONTRACT_TEXT, tool_call_id="call-1"))],
        awrap_tool_call=middleware.awrap_tool_call,
    )

    result = await node.ainvoke(_state(), config=_config())

    messages = _messages(result)
    envelope = _envelope(messages[0].content)
    assert envelope["status"] == "ok"
    assert envelope["agent"] == "procurement-analyst"
    assert envelope["report_path"] == "/analysis/runtime.md"


@pytest.mark.anyio
async def test_toolnode_subagent_exception_is_wrapped():
    middleware = DelegationContractMiddleware(
        known_subagents=("procurement-analyst",),
        logger=_Logger(),
    )

    async def boom(description: str, subagent_type: str) -> Command:
        raise RuntimeError("boom")

    tool = StructuredTool.from_function(
        coroutine=boom,
        name="task",
        description="fake task tool",
        args_schema=_TaskArgs,
    )
    node = ToolNode([tool], awrap_tool_call=middleware.awrap_tool_call)

    result = await node.ainvoke(_state(), config=_config())

    envelope = _envelope(_messages(result)[0].content)
    assert envelope["status"] == "error"
    assert envelope["retryable"] is False


def test_main_agent_registers_contract_middleware():
    """静态接线断言：真实 Agent 组装时必须注册契约中间件。"""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src/agent/main_agent.py"
    ).read_text(encoding="utf-8")
    assert "DelegationContractMiddleware(" in source
    assert "known_subagents=known_subagents or None" in source
    assert '"general-purpose"' in source
