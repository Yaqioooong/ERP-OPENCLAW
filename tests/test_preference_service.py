from __future__ import annotations

import asyncio

import pytest
from deepagents.backends.protocol import ReadResult, WriteResult

from agent.memory.preference_lock import (
    InMemoryPreferenceLock,
    PreferenceLockUnavailable,
)
from agent.memory.preference_service import PreferenceMemoryService
from agent.memory.preferences import dump_preferences, load_preferences
from tests.test_memory_middleware import MemoryStorageBackend


def make_service(backend, *, retries=2, lock=None):
    return PreferenceMemoryService(
        backend=backend,
        lock=lock or InMemoryPreferenceLock(),
        retries=retries,
    )


@pytest.mark.anyio
async def test_concurrent_auto_updates_keep_both():
    backend = MemoryStorageBackend()
    service = make_service(backend)

    first, second = await asyncio.gather(
        service.update_auto("u_a", ["供应商A"], "查询A"),
        service.update_auto("u_a", ["供应商B"], "查询B"),
    )
    assert first.ok and second.ok
    assert first.changed and second.changed
    assert {first.revision, second.revision} == {1, 2}

    prefs = load_preferences(backend.content).preferences
    assert set(prefs.recent_suppliers) == {"供应商A", "供应商B"}
    assert set(prefs.recent_queries) == {"查询A", "查询B"}
    assert prefs.revision == 2


@pytest.mark.anyio
async def test_read_exception_does_not_write():
    backend = MemoryStorageBackend(read_exception=RuntimeError("redis down"))
    service = make_service(backend)

    result = await service.update_auto("u_a", ["A"], "q")
    assert not result.ok
    assert result.reason == "read_failed"
    assert backend.writes == []


@pytest.mark.anyio
async def test_read_error_does_not_treat_as_empty():
    backend = MemoryStorageBackend("schema_version: 2\nrevision: 5\n")
    backend.read_exception = None
    # 让读取返回权限错误而不是 not found
    async def denied(path):
        backend.reads.append(path)
        return ReadResult(error="permission_denied")

    backend.aread = denied
    service = make_service(backend)
    result = await service.update_auto("u_a", ["A"], "q")
    assert result.reason == "read_failed"
    assert backend.writes == []


@pytest.mark.anyio
async def test_write_exception_returns_failure():
    backend = MemoryStorageBackend(write_exception=RuntimeError("aput failed"))
    service = make_service(backend)
    result = await service.update_auto("u_a", ["A"], "q")

    assert not result.ok
    assert result.reason == "write_failed"
    assert result.revision is None
    assert backend.content is None


@pytest.mark.anyio
async def test_write_result_error_returns_failure():
    backend = MemoryStorageBackend(write_error="permission_denied")
    service = make_service(backend)
    result = await service.update_auto("u_a", ["A"], "q")

    assert result.reason == "write_failed"
    assert result.revision is None


@pytest.mark.anyio
async def test_invalid_file_is_not_written():
    backend = MemoryStorageBackend(
        "schema_version: 2\nrevision: 0\npreferred_output: pie\n"
    )
    service = make_service(backend)
    result = await service.update_auto("u_a", ["A"], "q")
    assert result.reason == "invalid_schema"
    assert backend.writes == []


@pytest.mark.anyio
async def test_lock_timeout_returns_failure_without_write():
    class BusyLock:
        async def acquire(self, key, *, ttl_ms=None, wait_ms=None):
            return None

        async def release(self, key, token):
            return None

    backend = MemoryStorageBackend()
    service = make_service(backend, lock=BusyLock())
    result = await service.update_auto("u_a", ["A"], "q")
    assert result.reason == "lock_timeout"
    assert backend.writes == []


@pytest.mark.anyio
async def test_lock_unavailable_returns_failure_without_write():
    class DownLock:
        async def acquire(self, key, *, ttl_ms=None, wait_ms=None):
            raise PreferenceLockUnavailable("down")

        async def release(self, key, token):
            return None

    backend = MemoryStorageBackend()
    service = make_service(backend, lock=DownLock())
    result = await service.update_auto("u_a", ["A"], "q")
    assert result.reason == "lock_unavailable"
    assert backend.writes == []


@pytest.mark.anyio
async def test_explicit_update_preserves_auto_fields():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    await service.update_auto("u_a", ["供应商A"], "查询A")

    result = await service.save_explicit(
        "u_a",
        {"preferred_output": "table", "preferred_currency": "USD"},
    )
    assert result.ok
    assert result.changed
    prefs = load_preferences(backend.content).preferences
    assert prefs.preferred_output == "table"
    assert prefs.preferred_currency == "USD"
    assert prefs.recent_suppliers == ["供应商A"]
    assert prefs.revision == 2


@pytest.mark.anyio
async def test_explicit_invalid_field_is_rejected_without_write():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    result = await service.save_explicit("u_a", {"recent_suppliers": ["x"]})
    assert not result.ok
    assert result.reason == "invalid_request"
    assert backend.writes == []


@pytest.mark.anyio
async def test_explicit_invalid_enum_is_rejected_without_write():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    result = await service.save_explicit("u_a", {"preferred_output": "pie"})
    assert not result.ok
    assert result.reason == "invalid_request"
    assert backend.writes == []


@pytest.mark.anyio
async def test_revision_conflict_remerges_latest_content():
    class RacingBackend(MemoryStorageBackend):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def aread(self, path: str):
            result = await super().aread(path)
            self.calls += 1
            # 模拟另一个非协作写入者在第一次读取后把 revision 从 0 改为 1。
            if self.calls == 2 and self.content is None:
                from agent.memory.preferences import UserPreferences

                race = UserPreferences(recent_suppliers=["竞态供应商"], revision=1)
                self.content = dump_preferences(race)
                return ReadResult(file_data={"content": self.content})
            return result

    backend = RacingBackend()
    service = make_service(backend, retries=2)
    result = await service.update_auto("u_a", ["供应商A"], "查询A")

    assert result.ok
    prefs = load_preferences(backend.content).preferences
    assert set(prefs.recent_suppliers) == {"竞态供应商", "供应商A"}
    assert prefs.revision == 2


@pytest.mark.anyio
async def test_apply_candidates_enforces_origin_and_whitelist():
    backend = MemoryStorageBackend()
    service = make_service(backend)
    from agent.memory.candidates import MemoryCandidate

    candidates = [
        MemoryCandidate(
            memory_type="preferred_output",
            value="table",
            origin="explicit",
            source_message_id="h1",
            confidence=0.95,
            source="user_message",
        ),
        MemoryCandidate(
            memory_type="recent_suppliers",
            value="供应商A",
            origin="inferred",
            source_message_id="t1",
            confidence=0.9,
            source="tool_result",
        ),
        MemoryCandidate(
            memory_type="preferred_output",
            value="chart",
            origin="inferred",
            source_message_id="t1",
            confidence=1.0,
            source="tool_result",
        ),
        MemoryCandidate(
            memory_type="recent_suppliers",
            value="显式供应商",
            origin="explicit",
            source_message_id="h1",
            confidence=1.0,
            source="explicit_tool",
        ),
        MemoryCandidate(
            memory_type="recent_suppliers",
            value="低置信供应商",
            origin="inferred",
            source_message_id="t1",
            confidence=0.5,
            source="tool_result",
        ),
    ]
    result = await service.apply_candidates("u_a", candidates)
    assert result.ok
    assert result.applied_count == 2
    assert result.rejected_count == 3
    assert result.rejected_reasons == {"invalid_origin": 2, "low_confidence": 1}

    prefs = load_preferences(backend.content).preferences
    assert prefs.preferred_output == "table"
    assert prefs.recent_suppliers == ["供应商A"]


@pytest.mark.anyio
async def test_inferred_candidates_never_override_explicit_values():
    backend = MemoryStorageBackend(
        "schema_version: 2\nrevision: 2\npreferred_output: table\n"
        "preferred_chart_type: bar\n"
    )
    service = make_service(backend)
    from agent.memory.candidates import MemoryCandidate

    result = await service.apply_candidates(
        "u_a",
        [
            MemoryCandidate(
                memory_type="preferred_output",
                value="chart",
                origin="inferred",
                source_message_id="t1",
                confidence=1.0,
                source="tool_result",
            )
        ],
    )
    assert result.ok
    assert result.applied_count == 0
    assert result.rejected_count == 1
    prefs = load_preferences(backend.content).preferences
    assert prefs.preferred_output == "table"
    assert prefs.revision == 2
