from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_view.api import chat
from api_view.auth.dependencies import get_current_user
from api_view.auth.models import AuthenticatedUser
from api_view.auth.sessions import SessionStore, get_session_store
from api_view.thread_ownership import ThreadAccessError
from tests.fakes import FakeRedis


class FakeOwnership:
    def __init__(self, owners: dict[str, str] | None = None):
        self.owners = dict(owners or {})

    def claim(self, thread_id: str, user_id: str) -> None:
        if thread_id in self.owners and self.owners[thread_id] != user_id:
            raise ThreadAccessError("无权访问该会话")
        self.owners[thread_id] = user_id

    def verify(self, thread_id: str, user_id: str) -> None:
        if self.owners.get(thread_id) != user_id:
            raise ThreadAccessError("无权访问该会话")


def build_app(user: AuthenticatedUser | None, ownership: FakeOwnership):
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session_store] = lambda: SessionStore(
        FakeRedis(), ttl_seconds=60
    )
    return app


@pytest.fixture
def stream_calls(monkeypatch):
    calls: list[dict] = []

    async def fake_stream(**kwargs):
        calls.append(kwargs)
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(chat, "stream_chat_response", fake_stream)
    return calls


def test_missing_identity_returns_401_before_stream(stream_calls):
    app = build_app(None, FakeOwnership())
    response = TestClient(app).post("/api/chat/stream", json={"message": "hi"})
    assert response.status_code == 401
    assert stream_calls == []


def test_forged_user_header_cannot_change_identity(monkeypatch, stream_calls):
    user = AuthenticatedUser(user_id="u_a", username="alice", roles=("requester",))
    ownership = FakeOwnership()
    app = build_app(user, ownership)
    monkeypatch.setattr(chat, "get_thread_ownership_store", lambda: ownership)

    response = TestClient(app).post(
        "/api/chat/stream",
        json={"message": "hi"},
        headers={"X-User-Id": "u_b", "X-Username": "mallory"},
    )
    assert response.status_code == 200
    context = stream_calls[0]["context"]
    assert context.user_id == "u_a"
    assert context.username == "alice"
    assert ownership.owners[context.thread_id] == "u_a"


def test_user_b_cannot_resume_user_a_thread(monkeypatch, stream_calls):
    user_b = AuthenticatedUser(user_id="u_b", username="bob", roles=("requester",))
    ownership = FakeOwnership({"thread-a": "u_a"})
    app = build_app(user_b, ownership)
    monkeypatch.setattr(chat, "get_thread_ownership_store", lambda: ownership)

    response = TestClient(app).post(
        "/api/chat/thread-a/resume",
        json={"resume": {"supplement": "x"}},
    )
    assert response.status_code == 403
    assert stream_calls == []


def test_user_b_cannot_read_user_a_thread_state(monkeypatch, stream_calls):
    user_b = AuthenticatedUser(user_id="u_b", username="bob", roles=("requester",))
    ownership = FakeOwnership({"thread-a": "u_a"})
    app = build_app(user_b, ownership)
    monkeypatch.setattr(chat, "get_thread_ownership_store", lambda: ownership)

    assert TestClient(app).get("/api/chat/thread-a").status_code == 403
    assert TestClient(app).get("/api/chat/thread-a/history").status_code == 403
    assert stream_calls == []


def test_owner_can_continue_thread(monkeypatch, stream_calls):
    user = AuthenticatedUser(user_id="u_a", username="alice", roles=("requester",))
    ownership = FakeOwnership({"thread-a": "u_a"})
    app = build_app(user, ownership)
    monkeypatch.setattr(chat, "get_thread_ownership_store", lambda: ownership)

    response = TestClient(app).post(
        "/api/chat/stream",
        json={"message": "continue", "thread_id": "thread-a"},
    )
    assert response.status_code == 200
    assert stream_calls[0]["context"].thread_id == "thread-a"
    assert stream_calls[0]["context"].user_id == "u_a"


def test_legacy_unowned_thread_is_rejected(monkeypatch, stream_calls):
    user = AuthenticatedUser(user_id="u_a", username="alice", roles=("requester",))
    ownership = FakeOwnership()
    app = build_app(user, ownership)
    monkeypatch.setattr(chat, "get_thread_ownership_store", lambda: ownership)

    response = TestClient(app).post(
        "/api/chat/stream",
        json={"message": "old", "thread_id": "legacy-thread"},
    )
    assert response.status_code == 403
    assert stream_calls == []
    assert "legacy-thread" not in ownership.owners


def test_agent_loader_config_has_no_identity_default():
    from api_view.agent_loader import AgentLoader

    config = AgentLoader().create_config("thread-a")
    assert config == {"configurable": {"thread_id": "thread-a"}}
