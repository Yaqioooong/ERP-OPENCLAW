from __future__ import annotations

import pytest
from loguru import logger
from langgraph.store.memory import InMemoryStore

from agent.memory.audit import log_memory_event
from agent.memory.user_memory_backend import UserMemoryBackend


def make_backend(store, user_id: str) -> UserMemoryBackend:
    return UserMemoryBackend(store=store, namespace=lambda _rt: (user_id,))


@pytest.mark.anyio
async def test_user_a_write_is_invisible_to_user_b():
    store = InMemoryStore()
    a = make_backend(store, "u_a")
    b = make_backend(store, "u_b")

    written = await a.awrite("/u_a/preferences.md", "preferred_output: table\n")
    assert written.error is None

    own = await a.aread("/u_a/preferences.md")
    assert own.error is None
    assert "table" in (own.file_data or {}).get("content", "")

    other = await b.aread("/u_b/preferences.md")
    assert other.error is not None
    assert "table" not in str(other.file_data)


@pytest.mark.anyio
async def test_forged_path_is_denied_without_touching_store():
    store = InMemoryStore()
    backend = make_backend(store, "u_a")

    result = await backend.aread("/u_b/preferences.md")
    assert result.error == "permission_denied"

    write_result = await backend.awrite("/u_b/preferences.md", "secret")
    assert write_result.error == "permission_denied"

    # B 的命名空间中不应出现任何内容
    b = make_backend(store, "u_b")
    assert (await b.aread("/u_b/preferences.md")).error is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/u_b/preferences.md",
        "/u_a/../u_b/preferences.md",
        "/u_a//preferences.md",
        "/u_a/%2e%2e/u_b/preferences.md",
        "/u_a\\..\\u_b\\preferences.md",
    ],
)
async def test_path_variants_are_denied(path):
    store = InMemoryStore()
    backend = make_backend(store, "u_a")
    result = await backend.aread(path)
    assert result.error == "permission_denied"


@pytest.mark.anyio
async def test_ls_root_is_scoped_to_namespace():
    store = InMemoryStore()
    a = make_backend(store, "u_a")
    b = make_backend(store, "u_b")
    await a.awrite("/u_a/preferences.md", "a")
    await b.awrite("/u_b/preferences.md", "b")

    root = await a.als("/")
    assert root.error is None
    root_paths = [entry["path"] for entry in (root.entries or [])]
    assert root_paths == ["/u_a/"]
    assert not any(path.startswith("/u_b") for path in root_paths)

    listing = await a.als("/u_a/")
    assert listing.error is None
    paths = [entry["path"] for entry in (listing.entries or [])]
    assert paths == ["/u_a/preferences.md"]


@pytest.mark.anyio
async def test_denied_operation_is_logged_without_content():
    store = InMemoryStore()
    backend = make_backend(store, "u_a")
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        await backend.awrite("/u_b/preferences.md", "TOP-SECRET-CONTENT")
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "memory.write" in joined
    assert "u_a" in joined
    assert "path_not_owned" in joined
    assert "TOP-SECRET-CONTENT" not in joined


def test_audit_drops_unknown_and_content_fields():
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        log_memory_event(
            op="memory.write",
            user_id="u_a",
            namespace=("u_a",),
            path="/u_a/preferences.md",
            outcome="ok",
            content="SHOULD-NOT-APPEAR",
            suppliers=["SHOULD-NOT-APPEAR"],
        )
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "u_a" in joined
    assert "SHOULD-NOT-APPEAR" not in joined
