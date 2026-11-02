from __future__ import annotations

from types import SimpleNamespace

import pytest
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from deepagents.backends.protocol import ReadResult, WriteResult

from agent.memory.candidate_generator import MemoryCandidateGenerator
from agent.memory.preference_lock import InMemoryPreferenceLock
from agent.memory.preference_service import PreferenceMemoryService
from agent.memory.preferences import load_preferences
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.schema import ProcurementContext


class FakeModel:
    async def ainvoke(self, prompt):
        return AIMessage(content='{"suppliers": ["博世"], "query": "刹车片比价分析"}')


class EmptyModel:
    async def ainvoke(self, prompt):
        return AIMessage(content='{"suppliers": [], "query": ""}')


class MemoryStorageBackend:
    def __init__(
        self,
        content: str | None = None,
        *,
        read_exception: Exception | None = None,
        write_exception: Exception | None = None,
        write_error: str | None = None,
    ):
        self.content = content
        self.read_exception = read_exception
        self.write_exception = write_exception
        self.write_error = write_error
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, str]] = []

    async def aread(self, path: str):
        self.reads.append(path)
        if self.read_exception is not None:
            raise self.read_exception
        if self.content is None:
            return ReadResult(error=f"File '{path}' not found")
        return ReadResult(file_data={"content": self.content})

    async def write_preferences_internal(
        self, user_id: str, path: str, content: str
    ):
        self.writes.append((user_id, path, content))
        if self.write_exception is not None:
            raise self.write_exception
        if self.write_error is not None:
            return WriteResult(error=self.write_error)
        self.content = content
        return WriteResult(path=path)


def make_middleware(backend, model=None):
    service = PreferenceMemoryService(
        backend=backend,
        lock=InMemoryPreferenceLock(),
        retries=2,
    )
    return MemoryUpdateMiddleware(
        generator=MemoryCandidateGenerator(model or FakeModel()),
        preference_service=service,
    )


def runtime_for(user_id: str = "u_a"):
    context = ProcurementContext(
        user_id=user_id,
        username="alice",
        roles=("requester",),
        request_id="req-1",
        thread_id="thread-1",
    )
    return SimpleNamespace(context=context)


def _state():
    return {
        "messages": [
            HumanMessage(content="帮我对比博世刹车片的价格"),
            AIMessage(content="已完成比价，博世单价更低。"),
            ToolMessage(
                content=json.dumps(
                    {"ok": True, "data": {"name": "博世"}},
                    ensure_ascii=False,
                ),
                name="supplier_get",
                tool_call_id="tool-1",
            ),
        ]
    }


@pytest.mark.anyio
async def test_middleware_writes_only_own_preferences_path():
    backend = MemoryStorageBackend()
    middleware = make_middleware(backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert backend.reads[0] == "/u_a/preferences.md"
    assert len(backend.writes) == 1
    user_id, path, content = backend.writes[0]
    assert user_id == "u_a"
    assert path == "/u_a/preferences.md"
    assert "recent_suppliers" in content
    assert "博世" in content
    prefs = load_preferences(content).preferences
    assert prefs.revision == 1
    assert prefs.schema_version == 2


@pytest.mark.anyio
async def test_middleware_missing_identity_refuses_all_store_ops():
    backend = MemoryStorageBackend()
    middleware = make_middleware(backend)
    await middleware.aafter_agent(_state(), SimpleNamespace(context=None))

    assert backend.reads == []
    assert backend.writes == []


@pytest.mark.anyio
async def test_middleware_does_not_log_content():
    from loguru import logger

    backend = MemoryStorageBackend()
    middleware = make_middleware(backend)
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(_state(), runtime_for("u_a"))
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "req-1" in joined
    assert "博世" not in joined
    assert "刹车片" not in joined


@pytest.mark.anyio
async def test_invalid_preferences_are_not_written():
    backend = MemoryStorageBackend(
        "schema_version: 2\nrevision: 0\npreferred_output: pie\n"
    )
    middleware = make_middleware(backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))
    assert backend.writes == []


@pytest.mark.anyio
async def test_unknown_field_is_not_written_and_value_is_not_logged():
    from loguru import logger

    backend = MemoryStorageBackend(
        "schema_version: 2\nrevision: 0\npreferred_output: table\n"
        "secret_field: TOP_SECRET_VALUE\n"
    )
    middleware = make_middleware(backend)
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(_state(), runtime_for("u_a"))
    finally:
        logger.remove(sink_id)

    assert backend.writes == []
    joined = "\n".join(messages)
    assert "invalid_schema" in joined
    assert "TOP_SECRET_VALUE" not in joined


@pytest.mark.anyio
async def test_auto_update_preserves_explicit_preferences():
    backend = MemoryStorageBackend(
        "schema_version: 2\n"
        "revision: 7\n"
        "preferred_output: table\n"
        "preferred_chart_type: line\n"
        "preferred_currency: USD\n"
        "preferred_language: en\n"
        "recent_suppliers: []\n"
        "recent_queries: []\n"
    )
    middleware = make_middleware(backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert len(backend.writes) == 1
    _, _, content = backend.writes[0]
    prefs = load_preferences(content).preferences
    assert prefs.preferred_output == "table"
    assert prefs.preferred_chart_type == "line"
    assert prefs.preferred_currency == "USD"
    assert prefs.preferred_language == "en"
    assert prefs.schema_version == 2
    assert prefs.revision == 8
    assert "博世" in prefs.recent_suppliers


@pytest.mark.anyio
async def test_legacy_preferences_are_migrated_even_without_new_entities():
    backend = MemoryStorageBackend("schema_version: 1\npreferred_output: chart\n")
    middleware = make_middleware(backend, model=EmptyModel())
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert len(backend.writes) == 1
    _, _, content = backend.writes[0]
    loaded = load_preferences(content)
    assert loaded.migrated is False
    assert loaded.preferences.schema_version == 2
    assert loaded.preferences.revision == 1
    assert loaded.preferences.preferred_output == "chart"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        "schema_version: 2\npreferred_output: table\n  bad: x\n",
        "schema_version: 2\npreferred_output: pie\n",
        "schema_version: 2\nrecent_suppliers: not-a-list\n",
    ],
)
async def test_invalid_preference_structures_never_write(content):
    backend = MemoryStorageBackend(content)
    middleware = make_middleware(backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))
    assert backend.writes == []


@pytest.mark.anyio
async def test_write_failure_does_not_log_updated():
    from loguru import logger

    backend = MemoryStorageBackend(write_exception=RuntimeError("boom"))
    middleware = make_middleware(backend)
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(_state(), runtime_for("u_a"))
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "commit failed" in joined
    assert "updated user=" not in joined
