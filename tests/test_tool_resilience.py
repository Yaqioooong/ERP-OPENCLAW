"""工具调用异常/超时兜底测试（规格 specs/011）。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middlewares.tool_resilience import (
    TOOL_FAILED,
    TOOL_TIMEOUT,
    ToolResilienceMiddleware,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def info(self, msg: str, *args) -> None:
        self.events.append(("info", msg % args if args else msg))

    def warning(self, msg: str, *args) -> None:
        self.events.append(("warning", msg % args if args else msg))


def _request(tool_name: str, tool_call_id: str = "call-1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": tool_name,
            "args": {"query": "x"},
            "id": tool_call_id,
        },
        tool=None,
        state={},
        runtime=SimpleNamespace(context=None),
    )


def _envelope(content: str) -> dict:
    block = content.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(block)


@pytest.mark.anyio
async def test_read_timeout_retries_once_then_returns_error():
    calls: list[int] = []

    async def handler(request):
        calls.append(1)
        raise asyncio.TimeoutError()

    middleware = ToolResilienceMiddleware(
        timeout_by_tool={"part_search": 0.01},
        retry_delay_seconds=0,
        logger=_Logger(),
    )
    result = await middleware.awrap_tool_call(_request("part_search"), handler)

    assert len(calls) == 2
    envelope = _envelope(result.content)
    assert envelope["status"] == "timeout"
    assert envelope["code"] == TOOL_TIMEOUT
    assert envelope["retryable"] is True
    assert envelope["side_effect_unknown"] is False
    assert envelope["attempts"] == 2
    assert result.status == "error"


@pytest.mark.anyio
async def test_read_transient_error_is_retried_then_succeeds():
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("mcp disconnected")
        return ToolMessage(content="ok", tool_call_id="call-1")

    middleware = ToolResilienceMiddleware(retry_delay_seconds=0, logger=_Logger())
    result = await middleware.awrap_tool_call(_request("part_search"), handler)

    assert calls["count"] == 2
    assert result.content == "ok"


@pytest.mark.anyio
async def test_read_non_transient_error_is_not_retried():
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        raise ValueError("bad arguments")

    middleware = ToolResilienceMiddleware(retry_delay_seconds=0, logger=_Logger())
    result = await middleware.awrap_tool_call(_request("part_search"), handler)

    assert calls["count"] == 1
    envelope = _envelope(result.content)
    assert envelope["code"] == TOOL_FAILED
    assert envelope["retryable"] is False
    assert envelope["attempts"] == 1


@pytest.mark.anyio
async def test_write_timeout_is_not_retried_and_marks_side_effect_unknown():
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        await asyncio.sleep(0.2)

    middleware = ToolResilienceMiddleware(
        timeout_by_tool={"order_update": 0.01},
        retry_delay_seconds=0,
        logger=_Logger(),
    )
    result = await middleware.awrap_tool_call(_request("order_update"), handler)

    assert calls["count"] == 1
    envelope = _envelope(result.content)
    assert envelope["status"] == "timeout"
    assert envelope["retryable"] is False
    assert envelope["side_effect_unknown"] is True
    assert envelope["attempts"] == 1
    assert "order_search_details" in result.content


@pytest.mark.anyio
async def test_write_exception_is_not_retried():
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        raise RuntimeError("remote write failed")

    middleware = ToolResilienceMiddleware(retry_delay_seconds=0, logger=_Logger())
    result = await middleware.awrap_tool_call(_request("order_create"), handler)

    assert calls["count"] == 1
    envelope = _envelope(result.content)
    assert envelope["status"] == "error"
    assert envelope["retryable"] is False
    assert envelope["side_effect_unknown"] is True


@pytest.mark.anyio
async def test_graph_interrupt_propagates():
    async def handler(request):
        raise GraphInterrupt()

    middleware = ToolResilienceMiddleware(logger=_Logger())
    with pytest.raises(GraphInterrupt):
        await middleware.awrap_tool_call(_request("order_update"), handler)


@pytest.mark.anyio
async def test_cancelled_error_propagates():
    async def handler(request):
        raise asyncio.CancelledError()

    middleware = ToolResilienceMiddleware(logger=_Logger())
    with pytest.raises(asyncio.CancelledError):
        await middleware.awrap_tool_call(_request("web_search"), handler)


@pytest.mark.anyio
async def test_task_is_skipped():
    payload = ToolMessage(content="task result", tool_call_id="call-1")
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        return payload

    middleware = ToolResilienceMiddleware(logger=_Logger())
    result = await middleware.awrap_tool_call(_request("task"), handler)

    assert result is payload
    assert calls["count"] == 1


@pytest.mark.anyio
async def test_success_result_passes_through():
    payload = ToolMessage(content="ok", tool_call_id="call-1")

    async def handler(request):
        return payload

    middleware = ToolResilienceMiddleware(logger=_Logger())
    result = await middleware.awrap_tool_call(_request("part_search"), handler)

    assert result is payload


def test_sync_exception_fallback():
    def handler(request):
        raise RuntimeError("boom")

    middleware = ToolResilienceMiddleware(logger=_Logger())
    result = middleware.wrap_tool_call(_request("part_search"), handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert _envelope(result.content)["retryable"] is False


def test_main_and_subagents_register_tool_resilience():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    main_source = (root / "src/agent/main_agent.py").read_text(encoding="utf-8")
    config_source = (root / "src/agent/middleware_config.py").read_text(
        encoding="utf-8"
    )

    assert "ToolResilienceMiddleware(skip_tools=(\"task\",))" in main_source
    assert config_source.count("ToolResilienceMiddleware()") == 2


@pytest.mark.anyio
async def test_toolnode_tool_exception_returns_error_message():
    """真实 ToolNode 路径：工具异常不再向上抛，模型拿到 error ToolMessage。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
    from langgraph.prebuilt.tool_node import ToolNode
    from langgraph.runtime import Runtime
    from pydantic import BaseModel, Field

    class Args(BaseModel):
        query: str = Field(...)

    async def failing_tool(query: str):
        raise RuntimeError("mcp transport down")

    tool = StructuredTool.from_function(
        coroutine=failing_tool,
        name="part_search",
        description="fake search",
        args_schema=Args,
    )
    middleware = ToolResilienceMiddleware(retry_delay_seconds=0, logger=_Logger())
    node = ToolNode([tool], awrap_tool_call=middleware.awrap_tool_call)
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "part_search",
                        "args": {"query": "x"},
                        "id": "call-1",
                    }
                ],
            )
        ]
    }
    config = {
        CONF: {
            CONFIG_KEY_RUNTIME: Runtime(
                context=SimpleNamespace(user_id="u-1"), store=None
            )
        }
    }

    result = await node.ainvoke(state, config=config)
    messages = result["messages"] if isinstance(result, dict) else result
    message = messages[0]
    assert message.status == "error"
    assert _envelope(message.content)["code"] == TOOL_FAILED
