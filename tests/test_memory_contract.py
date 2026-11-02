from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.memory.metrics import metrics
from agent.memory.preference_lock import InMemoryPreferenceLock
from agent.memory.preference_service import PreferenceMemoryService
from agent.memory.preferences import load_preferences, recent_values
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from tests.test_memory_middleware import MemoryStorageBackend


class NoopModel:
    async def ainvoke(self, prompt):
        return AIMessage(content='{"candidates": []}')


def _state():
    return {
        "messages": [
            HumanMessage(content="你好，帮我比较供应商", id="msg-user"),
            AIMessage(content="建议关注 C 公司"),
            ToolMessage(
                content=json.dumps(
                    {"ok": True, "data": {"name": "C 公司"}},
                    ensure_ascii=False,
                ),
                name="supplier_get",
                tool_call_id="tool-1",
            ),
        ]
    }


def _middleware(backend):
    return MemoryUpdateMiddleware(
        generator=__import__(
            "agent.memory.candidate_generator",
            fromlist=["MemoryCandidateGenerator"],
        ).MemoryCandidateGenerator(NoopModel()),
        preference_service=PreferenceMemoryService(
            backend=backend,
            lock=InMemoryPreferenceLock(),
            retries=1,
        ),
    )


@pytest.mark.anyio
async def test_first_use_creates_preferences_file():
    backend = MemoryStorageBackend()
    middleware = _middleware(backend)
    from types import SimpleNamespace
    from agent.schema import ProcurementContext

    context = ProcurementContext(
        user_id="u_new", username="new", request_id="req", thread_id="thread"
    )
    metrics.reset()
    try:
        await middleware.aafter_agent(_state(), SimpleNamespace(context=context))
        assert (
            metrics.snapshot()["counters"].get("memory_candidates_total") == 2
        )
    finally:
        metrics.reset()

    assert len(backend.writes) == 1
    prefs = load_preferences(backend.content).preferences
    assert prefs.schema_version == 3
    assert recent_values(prefs.recent_suppliers) == ["C 公司"]


@pytest.mark.anyio
async def test_auto_update_log_has_message_ids():
    from loguru import logger
    from types import SimpleNamespace
    from agent.schema import ProcurementContext

    backend = MemoryStorageBackend()
    middleware = _middleware(backend)
    context = ProcurementContext(
        user_id="u_a", username="alice", request_id="req", thread_id="thread"
    )
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(_state(), SimpleNamespace(context=context))
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "memory.update_preferences" in joined
    assert "msg-user" in joined
    assert "tool-1" in joined
    assert "C 公司" not in joined


def test_session_delete_and_memory_management_are_separate():
    history_source = (
        Path(__file__).resolve().parents[1] / "src/api_view/api/history.py"
    ).read_text(encoding="utf-8")
    assert "preferences" not in history_source
    assert "PreferenceMemoryService" not in history_source


def test_procurement_does_not_depend_on_long_term_preferences():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/agent/procurement/task_runner.py",
        "src/agent/procurement/nodes.py",
        "src/agent/procurement/approval_repository.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "recent_suppliers" not in source
        assert "PreferenceMemoryService" not in source


@pytest.mark.anyio
async def test_no_supplier_candidate_without_tool_result():
    from agent.memory.candidate_generator import MemoryCandidateGenerator

    result = await MemoryCandidateGenerator().generate(
        [HumanMessage(content="A 公司看起来不错，帮我看看", id="h1")],
        thread_id="t",
    )
    assert all(c.memory_type != "recent_suppliers" for c in result)
