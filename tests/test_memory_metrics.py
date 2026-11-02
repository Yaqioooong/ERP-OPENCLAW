from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.memory.metrics import metrics
from agent.memory.preference_lock import InMemoryPreferenceLock
from agent.memory.preference_service import PreferenceMemoryService
from api_view.api.memory import get_memory_service, router
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready
from tests.test_memory_middleware import MemoryStorageBackend


@pytest.fixture(autouse=True)
def reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def make_service(backend, *, retries=1):
    return PreferenceMemoryService(
        backend=backend,
        lock=InMemoryPreferenceLock(),
        retries=retries,
    )


def counter(name: str) -> int:
    return metrics.snapshot()["counters"].get(name, 0)


@pytest.mark.anyio
async def test_read_success_and_failure_metrics():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await service.get_preferences("u_a")
    assert counter("memory_read_success_total") == 1

    failing = MemoryStorageBackend(read_exception=RuntimeError("down"))
    with pytest.raises(Exception):
        await make_service(failing).get_preferences("u_a")
    assert counter("memory_read_failure_total") == 1


@pytest.mark.anyio
async def test_parse_failure_metric():
    backend = MemoryStorageBackend("schema_version: 3\npreferred_output: pie\n")
    service = make_service(backend)
    with pytest.raises(Exception):
        await service.get_preferences("u_a")
    assert counter("memory_parse_failure_total") == 1
    assert counter("memory_read_failure_total") == 1


@pytest.mark.anyio
async def test_write_success_and_failure_metrics():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await service.update_auto("u_a", ["A"], "q")
    assert counter("memory_write_success_total") == 1

    failing = MemoryStorageBackend(write_exception=RuntimeError("aput"))
    result = await make_service(failing).update_auto("u_a", ["A"], "q")
    assert not result.ok
    assert counter("memory_write_failure_total") == 1


@pytest.mark.anyio
async def test_conflict_metric():
    class RacingBackend(MemoryStorageBackend):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def aread(self, path):
            result = await super().aread(path)
            self.calls += 1
            if self.calls == 2:
                from agent.memory.preferences import UserPreferences
                from agent.memory.preferences import dump_preferences

                self.content = dump_preferences(UserPreferences(revision=1))
                from deepagents.backends.protocol import ReadResult

                return ReadResult(file_data={"content": self.content})
            return result

    result = await make_service(RacingBackend(), retries=0).update_auto(
        "u_a", ["A"], "q"
    )
    assert result.reason == "conflict"
    assert counter("memory_write_conflict_total") == 1


@pytest.mark.anyio
async def test_candidate_rejected_and_explicit_failure_metrics():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    from agent.memory.candidates import MemoryCandidate
    from agent.memory.preference_service import _failure

    await service.apply_candidates(
        "u_a",
        [
            MemoryCandidate(
                memory_type="recent_suppliers",
                value="low",
                origin="inferred",
                source_message_id="t1",
                confidence=0.1,
                source="tool_result",
            )
        ],
    )
    assert (
        metrics.snapshot()["counters"].get(
            "memory_candidates_rejected_total{reason=low_confidence}"
        )
        == 1
    )

    await service.save_explicit("u_a", {"preferred_output": "pie"})
    assert counter("memory_explicit_save_failure_total") == 1


@pytest.mark.anyio
async def test_metrics_endpoint_requires_admin_and_returns_counts():
    service = make_service(MemoryStorageBackend())
    admin = AuthenticatedUser(user_id="u_admin", username="admin", roles=("admin",))
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_password_ready] = lambda: admin
    app.dependency_overrides[get_memory_service] = lambda: service
    metrics.inc("memory_candidates_total", value=3)

    response = TestClient(app).get("/api/memory/metrics")
    assert response.status_code == 200
    assert response.json()["counters"]["memory_candidates_total"] == 3
