"""模型重试/降级测试（规格 specs/012）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphInterrupt

from agent.model_resilience import (
    build_model_fallback_middleware,
    build_model_retry_middleware,
    is_retryable_model_error,
)


class _StatusError(RuntimeError):
    def __init__(self, status_code: int, message: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code


def _model(name: str = "primary") -> ChatOpenAI:
    return ChatOpenAI(
        model=name,
        api_key="test-key",
        base_url="http://127.0.0.1:1/v1",
        timeout=1,
        max_retries=0,
    )


def _request(model: ChatOpenAI) -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[HumanMessage(content="hello")],
        system_message=None,
        tool_choice=None,
        tools=[],
        response_format=None,
        state={},
        runtime=None,
        model_settings={},
    )


def test_retryable_classification():
    assert is_retryable_model_error(TimeoutError("timed out"))
    assert is_retryable_model_error(ConnectionError("connection reset"))
    assert is_retryable_model_error(_StatusError(429))
    assert is_retryable_model_error(_StatusError(503))
    assert not is_retryable_model_error(ValueError("bad request"))
    assert not is_retryable_model_error(_StatusError(400))
    assert not is_retryable_model_error(_StatusError(401))


@pytest.mark.anyio
async def test_retry_exhausts_then_fallback():
    primary = _model("primary")
    fallback = _model("fallback")
    attempts = {"primary": 0, "fallback": 0}

    async def handler(request: ModelRequest):
        if request.model is primary:
            attempts["primary"] += 1
            raise TimeoutError("primary timeout")
        attempts["fallback"] += 1
        return AIMessage(content="fallback answer")

    fallback_mw = build_model_fallback_middleware(fallback)
    retry_mw = build_model_retry_middleware(
        max_retries=2,
        initial_delay=0,
        max_delay=0,
    )

    async def retry_then_primary(request):
        return await retry_mw.awrap_model_call(request, handler)

    result = await fallback_mw.awrap_model_call(
        _request(primary), retry_then_primary
    )

    assert result.content == "fallback answer"
    assert attempts["primary"] == 3
    assert attempts["fallback"] == 1


@pytest.mark.anyio
async def test_non_retryable_error_is_not_retried():
    model = _model()
    calls = {"count": 0}

    async def handler(request):
        calls["count"] += 1
        raise ValueError("bad arguments")

    retry_mw = build_model_retry_middleware(
        max_retries=3,
        initial_delay=0,
        max_delay=0,
    )

    with pytest.raises(ValueError):
        await retry_mw.awrap_model_call(_request(model), handler)

    assert calls["count"] == 1


@pytest.mark.anyio
async def test_fallback_also_fails_raises_last_exception():
    primary = _model("primary")
    fallback = _model("fallback")

    async def handler(request):
        raise RuntimeError("model down")

    fallback_mw = build_model_fallback_middleware(fallback)

    with pytest.raises(RuntimeError, match="model down"):
        await fallback_mw.awrap_model_call(_request(primary), handler)


@pytest.mark.anyio
async def test_graph_interrupt_is_not_swallowed_by_fallback():
    primary = _model("primary")
    fallback = _model("fallback")

    async def handler(request):
        raise GraphInterrupt()

    fallback_mw = build_model_fallback_middleware(fallback)

    with pytest.raises(GraphInterrupt):
        await fallback_mw.awrap_model_call(_request(primary), handler)


@pytest.mark.anyio
async def test_cancelled_error_is_not_swallowed_by_fallback():
    primary = _model("primary")
    fallback = _model("fallback")

    async def handler(request):
        raise asyncio.CancelledError()

    fallback_mw = build_model_fallback_middleware(fallback)

    with pytest.raises(asyncio.CancelledError):
        await fallback_mw.awrap_model_call(_request(primary), handler)


def test_model_and_wiring_sources():
    root = Path(__file__).resolve().parents[1]
    config = (root / "src/agent/config.py").read_text(encoding="utf-8")
    main_agent = (root / "src/agent/main_agent.py").read_text(encoding="utf-8")
    middleware_config = (root / "src/agent/middleware_config.py").read_text(
        encoding="utf-8"
    )
    dependencies = (
        root / "src/agent/procurement/dependencies.py"
    ).read_text(encoding="utf-8")

    # 配置：显式 timeout/重试 + 备用模型 + 摘要 fallback
    assert "MODEL_REQUEST_TIMEOUT_SECONDS" in config
    assert "timeout=MODEL_REQUEST_TIMEOUT_SECONDS" in config
    assert "max_retries=0" in config
    assert "SUMMARY_MODEL_FALLBACK = SUMMARY_MODEL.with_fallbacks" in config

    # 主 Agent：fallback/retry 接线 + 摘要直调 fallback
    assert "build_model_fallback_middleware(FALLBACK_MODEL)" in main_agent
    assert "build_model_retry_middleware(" in main_agent
    assert "MemoryCandidateGenerator(SUMMARY_MODEL_FALLBACK)" in main_agent

    # 两个采购子 Agent 都接线
    assert "_model_resilience_middleware()" in middleware_config
    assert middleware_config.count("_model_resilience_middleware()") >= 2

    # 采购解析/报告解释直调路径
    assert dependencies.count("SUMMARY_MODEL_FALLBACK") >= 2
