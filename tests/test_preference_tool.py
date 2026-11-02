from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.memory.preference_service import MemoryCommitResult
from agent.schema import ProcurementContext
from agent.tools.preference_tools import build_preference_tools


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def save_explicit(
        self, user_id, updates, *, request_id="", thread_id="", source_message_id=""
    ):
        self.calls.append((user_id, updates, request_id, thread_id, source_message_id))
        return self.result


def make_runtime():
    context = ProcurementContext(
        user_id="u_a",
        username="alice",
        roles=("requester",),
        request_id="req-1",
        thread_id="thread-1",
    )
    return SimpleNamespace(context=context)


@pytest.mark.anyio
async def test_tool_returns_saved_true():
    service = FakeService(
        MemoryCommitResult(ok=True, revision=4, reason="ok", changed=True)
    )
    tool = build_preference_tools(service)[0]
    raw = await tool.coroutine({"preferred_output": "table"}, make_runtime())
    data = json.loads(raw)
    assert data == {"saved": True, "revision": 4, "changed": True}
    assert service.calls[0][0] == "u_a"


@pytest.mark.anyio
async def test_tool_returns_saved_false_for_write_failure():
    service = FakeService(
        MemoryCommitResult(
            ok=False,
            revision=None,
            reason="write_failed",
            retryable=True,
            message="偏好写入失败，本次未保存",
        )
    )
    tool = build_preference_tools(service)[0]
    raw = await tool.coroutine({"preferred_output": "table"}, make_runtime())
    data = json.loads(raw)
    assert data["saved"] is False
    assert data["reason"] == "write_failed"
    assert data["retryable"] is True
    assert "未保存" in data["message"]


@pytest.mark.anyio
async def test_tool_rejects_auto_fields_and_unknown_fields():
    service = FakeService(
        MemoryCommitResult(ok=True, revision=1, reason="ok", changed=True)
    )
    tool = build_preference_tools(service)[0]

    for updates in (
        {"recent_suppliers": ["x"]},
        {"unknown": "x"},
        {},
    ):
        raw = await tool.coroutine(updates, make_runtime())
        data = json.loads(raw)
        assert data["saved"] is False
        assert data["reason"] == "invalid_request"

    assert service.calls == []


@pytest.mark.anyio
async def test_tool_missing_identity_returns_saved_false():
    service = FakeService(
        MemoryCommitResult(ok=True, revision=1, reason="ok", changed=True)
    )
    tool = build_preference_tools(service)[0]
    raw = await tool.coroutine(
        {"preferred_output": "table"}, SimpleNamespace(context=None)
    )
    data = json.loads(raw)
    assert data["saved"] is False
    assert data["reason"] == "permission_denied"


@pytest.mark.anyio
async def test_tool_reports_real_service_write_failure():
    from agent.memory.preference_lock import InMemoryPreferenceLock
    from agent.memory.preference_service import PreferenceMemoryService
    from tests.test_memory_middleware import MemoryStorageBackend

    backend = MemoryStorageBackend(write_exception=RuntimeError("aput failed"))
    service = PreferenceMemoryService(
        backend=backend, lock=InMemoryPreferenceLock(), retries=1
    )
    tool = build_preference_tools(service)[0]
    raw = await tool.coroutine({"preferred_output": "table"}, make_runtime())
    data = json.loads(raw)
    assert data["saved"] is False
    assert data["reason"] == "write_failed"
    assert backend.writes
