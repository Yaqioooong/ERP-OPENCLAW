from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore

from agent.memory.user_memory_backend import UserMemoryBackend
from agent.middlewares.memory_update import MemoryUpdateMiddleware
from agent.schema import ProcurementContext


class FakeModel:
    async def ainvoke(self, prompt):
        return AIMessage(
            content='{"suppliers": ["博世"], "query": "刹车片比价分析"}'
        )


class RecordingBackend:
    def __init__(self):
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    async def aread(self, path: str):
        self.reads.append(path)
        from deepagents.backends.protocol import ReadResult

        return ReadResult(error=f"File '{path}' not found")

    async def awrite(self, path: str, content: str):
        self.writes.append((path, content))
        from deepagents.backends.protocol import WriteResult

        return WriteResult(path=path)


def runtime_for(user_id: str = "u_a"):
    context = ProcurementContext(
        user_id=user_id,
        username="alice",
        roles=("requester",),
        request_id="req-1",
        thread_id="thread-1",
    )
    return SimpleNamespace(context=context)


@pytest.mark.anyio
async def test_middleware_writes_only_own_preferences_path():
    backend = RecordingBackend()
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    state = {
        "messages": [
            HumanMessage(content="帮我对比博世刹车片的价格"),
            AIMessage(content="已完成比价，博世单价更低。"),
        ]
    }
    await middleware.aafter_agent(state, runtime_for("u_a"))

    assert backend.reads == ["/memories/u_a/preferences.md"]
    assert len(backend.writes) == 1
    path, content = backend.writes[0]
    assert path == "/memories/u_a/preferences.md"
    assert "recent_suppliers" in content
    assert "博世" in content


@pytest.mark.anyio
async def test_middleware_missing_identity_refuses_all_store_ops():
    backend = RecordingBackend()
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    state = {
        "messages": [
            HumanMessage(content="帮我对比博世刹车片的价格"),
            AIMessage(content="已完成比价。"),
        ]
    }
    await middleware.aafter_agent(state, SimpleNamespace(context=None))

    assert backend.reads == []
    assert backend.writes == []


@pytest.mark.anyio
async def test_middleware_does_not_log_content():
    from loguru import logger

    backend = RecordingBackend()
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    state = {
        "messages": [
            HumanMessage(content="帮我对比博世刹车片的价格"),
            AIMessage(content="已完成比价。"),
        ]
    }
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(state, runtime_for("u_a"))
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "req-1" in joined
    assert "博世" not in joined
    assert "刹车片" not in joined


@pytest.mark.anyio
async def test_composite_backend_routes_to_user_namespace():
    from deepagents.backends import CompositeBackend, StoreBackend

    store = InMemoryStore()
    backend = CompositeBackend(
        default=StoreBackend(
            store=store, namespace=lambda _rt: ("other",)
        ),
        routes={
            "/memories/": UserMemoryBackend(
                store=store, namespace=lambda _rt: ("u_a",)
            )
        },
    )
    result = await backend.awrite("/memories/u_a/preferences.md", "hello")
    assert result.error is None
    read = await backend.aread("/memories/u_a/preferences.md")
    assert read.error is None
    assert "hello" in (read.file_data or {}).get("content", "")

    forged = await backend.aread("/memories/u_b/preferences.md")
    assert forged.error == "permission_denied"


class EmptyModel:
    async def ainvoke(self, prompt):
        return AIMessage(content='{"suppliers": [], "query": ""}')


class StaticReadBackend(RecordingBackend):
    def __init__(self, content: str):
        super().__init__()
        self.content = content

    async def aread(self, path: str):
        self.reads.append(path)
        from deepagents.backends.protocol import ReadResult

        return ReadResult(file_data={"content": self.content})


def _state():
    return {
        "messages": [
            HumanMessage(content="帮我对比博世刹车片的价格"),
            AIMessage(content="已完成比价。"),
        ]
    }


@pytest.mark.anyio
async def test_invalid_preferences_are_not_written():
    backend = StaticReadBackend("schema_version: 1\npreferred_output: pie\n")
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert backend.writes == []


@pytest.mark.anyio
async def test_unknown_field_is_not_written_and_value_is_not_logged():
    from loguru import logger

    backend = StaticReadBackend(
        "schema_version: 1\npreferred_output: table\nsecret_field: TOP_SECRET_VALUE\n"
    )
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await middleware.aafter_agent(_state(), runtime_for("u_a"))
    finally:
        logger.remove(sink_id)

    assert backend.writes == []
    joined = "\n".join(messages)
    assert "unknown_field" in joined
    assert "secret_field" in joined
    assert "TOP_SECRET_VALUE" not in joined


@pytest.mark.anyio
async def test_auto_update_preserves_explicit_preferences():
    backend = StaticReadBackend(
        "schema_version: 1\n"
        "preferred_output: table\n"
        "preferred_chart_type: line\n"
        "preferred_currency: USD\n"
        "preferred_language: en\n"
        "recent_suppliers: []\n"
        "recent_queries: []\n"
    )
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert len(backend.writes) == 1
    _, content = backend.writes[0]
    from agent.memory.preferences import load_preferences

    prefs = load_preferences(content).preferences
    assert prefs.preferred_output == "table"
    assert prefs.preferred_chart_type == "line"
    assert prefs.preferred_currency == "USD"
    assert prefs.preferred_language == "en"
    assert prefs.schema_version == 1
    assert "博世" in prefs.recent_suppliers


@pytest.mark.anyio
async def test_legacy_preferences_are_migrated_even_without_new_entities():
    backend = StaticReadBackend("preferred_output: chart\nrecent_suppliers: []\n")
    middleware = MemoryUpdateMiddleware(model=EmptyModel(), backend=backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))

    assert len(backend.writes) == 1
    _, content = backend.writes[0]
    from agent.memory.preferences import load_preferences

    loaded = load_preferences(content)
    assert loaded.migrated is False
    assert loaded.preferences.schema_version == 1
    assert loaded.preferences.preferred_output == "chart"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    [
        "schema_version: 1\npreferred_output: table\n  bad: x\n",
        "schema_version: 1\npreferred_output: pie\n",
        "schema_version: 1\nrecent_suppliers: not-a-list\n",
    ],
)
async def test_invalid_preference_structures_never_write(content):
    backend = StaticReadBackend(content)
    middleware = MemoryUpdateMiddleware(model=FakeModel(), backend=backend)
    await middleware.aafter_agent(_state(), runtime_for("u_a"))
    assert backend.writes == []
