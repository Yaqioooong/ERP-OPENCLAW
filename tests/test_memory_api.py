from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.memory.candidates import MemoryCandidate
from agent.memory.preference_lock import InMemoryPreferenceLock
from agent.memory.preference_service import PreferenceMemoryService
from agent.memory.preferences import load_preferences, recent_values
from api_view.api.memory import get_memory_service, router
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready
from tests.test_memory_middleware import MemoryStorageBackend


def build_app(service, user=None):
    user = user or AuthenticatedUser(user_id="u_a", username="alice")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_password_ready] = lambda: user
    app.dependency_overrides[get_memory_service] = lambda: service
    return TestClient(app)


def make_service(backend):
    return PreferenceMemoryService(
        backend=backend,
        lock=InMemoryPreferenceLock(),
        retries=1,
    )


async def seed(service):
    await service.apply_candidates(
        "u_a",
        [
            MemoryCandidate(
                memory_type="preferred_output",
                value="table",
                origin="explicit",
                source_message_id="h1",
                confidence=1.0,
                source="explicit_tool",
            ),
            MemoryCandidate(
                memory_type="recent_suppliers",
                value="A",
                origin="inferred",
                source_message_id="t1",
                confidence=0.9,
                source="tool_result",
            ),
            MemoryCandidate(
                memory_type="recent_suppliers",
                value="B",
                origin="inferred",
                source_message_id="t1",
                confidence=0.9,
                source="tool_result",
            ),
            MemoryCandidate(
                memory_type="recent_queries",
                value="查询A",
                origin="inferred",
                source_message_id="h1",
                confidence=0.7,
                source="user_message",
            ),
        ],
    )


@pytest.mark.anyio
async def test_get_preferences_view():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await seed(service)
    client = build_app(service)
    response = client.get("/api/memory/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["preferred"]["preferred_output"] == "table"
    assert [item["value"] for item in data["recent_suppliers"]] == ["A", "B"]
    assert data["recent_suppliers"][0]["expires_at"]
    assert data["limits"]["supplier_ttl_days"] == 90


@pytest.mark.anyio
async def test_delete_single_item_keeps_others():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await seed(service)
    client = build_app(service)

    response = client.request(
        "DELETE",
        "/api/memory/preferences/items",
        json={"memory_type": "recent_suppliers", "value": "A"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    prefs = load_preferences(backend.content).preferences
    assert recent_values(prefs.recent_suppliers) == ["B"]
    assert prefs.preferred_output == "table"
    assert recent_values(prefs.recent_queries) == ["查询A"]


@pytest.mark.anyio
async def test_delete_missing_item_returns_404():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    client = build_app(service)
    response = client.request(
        "DELETE",
        "/api/memory/preferences/items",
        json={"memory_type": "recent_suppliers", "value": "missing"},
    )
    assert response.status_code == 404
    assert backend.writes == []


@pytest.mark.anyio
async def test_clear_preferences():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await seed(service)
    client = build_app(service)
    response = client.delete("/api/memory/preferences")
    assert response.status_code == 200
    assert response.json()["cleared"] is True

    prefs = load_preferences(backend.content).preferences
    assert prefs.preferred_output is None
    assert prefs.recent_suppliers == []
    assert prefs.recent_queries == []


@pytest.mark.anyio
async def test_delete_audit_does_not_log_value():
    from loguru import logger

    backend = MemoryStorageBackend()
    service = make_service(backend)
    await seed(service)
    client = build_app(service)
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        client.request(
            "DELETE",
            "/api/memory/preferences/items",
            json={"memory_type": "recent_suppliers", "value": "A"},
        )
    finally:
        logger.remove(sink_id)

    joined = "\n".join(messages)
    assert "memory.delete_item" in joined
    assert "recent_suppliers" in joined
    assert '"A"' not in joined
