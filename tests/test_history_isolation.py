from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_view.api import history
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready
from api_view.thread_ownership import ThreadAccessError
from tests.fakes import FakeRedis


class FakeOwnership:
    def __init__(self):
        self.owners = {"thread-a": "u_a", "thread-b": "u_b"}
        self.deleted: list[str] = []
        self.released: list[str] = []

    def get_owner(self, thread_id):
        return self.owners.get(thread_id)

    def release(self, thread_id):
        self.released.append(thread_id)
        self.owners.pop(thread_id, None)


def build_app(user: AuthenticatedUser, monkeypatch, ownership: FakeOwnership):
    app = FastAPI()
    app.include_router(history.router, prefix="/api")
    app.dependency_overrides[require_password_ready] = lambda: user
    monkeypatch.setattr(history, "get_thread_ownership_store", lambda: ownership)
    monkeypatch.setattr(
        history.agent_loader, "get_all_thread_ids", lambda: ["thread-a", "thread-b", "legacy"]
    )
    async def fake_messages(thread_id):
        return _messages(thread_id)

    async def fake_display(thread_id):
        return _display(thread_id)

    monkeypatch.setattr(history.agent_loader, "get_current_messages", fake_messages)
    monkeypatch.setattr(history.agent_loader, "get_display_messages", fake_display)
    monkeypatch.setattr(
        history.agent_loader, "get_session_updated_at", lambda thread_id: _now()
    )

    async def fake_delete(thread_id):
        ownership.deleted.append(thread_id)
        return True

    monkeypatch.setattr(history.agent_loader, "delete_session", fake_delete)
    return TestClient(app)


def _messages(thread_id):
    return [{"role": "user", "content": f"secret-{thread_id}"}]


def _display(thread_id):
    return [{"id": "1", "role": "user", "content": f"secret-{thread_id}"}]


def _now():
    from datetime import datetime

    return datetime.now()


def test_requester_only_sees_own_sessions(monkeypatch):
    user = AuthenticatedUser(user_id="u_a", username="alice")
    client = build_app(user, monkeypatch, FakeOwnership())
    response = client.get("/api/history")
    assert response.status_code == 200
    threads = [item["thread_id"] for item in response.json()["sessions"]]
    assert threads == ["thread-a"]
    assert "secret-thread-b" not in response.text


def test_requester_cannot_read_or_delete_other_messages(monkeypatch):
    user = AuthenticatedUser(user_id="u_a", username="alice")
    ownership = FakeOwnership()
    client = build_app(user, monkeypatch, ownership)
    assert client.get("/api/history/thread-b/messages").status_code == 403
    assert client.delete("/api/history/thread-b").status_code == 403
    assert ownership.deleted == []
    assert client.get("/api/history/thread-a/messages").status_code == 200


def test_admin_sees_metadata_but_not_other_messages(monkeypatch):
    user = AuthenticatedUser(user_id="u_admin", username="admin", roles=("admin",))
    ownership = FakeOwnership()
    client = build_app(user, monkeypatch, ownership)
    listing = client.get("/api/history")
    assert listing.status_code == 200
    threads = [item["thread_id"] for item in listing.json()["sessions"]]
    assert set(threads) == {"thread-a", "thread-b", "legacy"}
    assert "secret-thread-a" not in listing.text
    assert "secret-thread-b" not in listing.text
    assert client.get("/api/history/thread-a/messages").status_code == 403
    assert client.delete("/api/history/thread-a").status_code == 200


def test_legacy_thread_not_visible_to_requester(monkeypatch):
    user = AuthenticatedUser(user_id="u_a", username="alice")
    client = build_app(user, monkeypatch, FakeOwnership())
    listing = client.get("/api/history")
    assert "legacy" not in [item["thread_id"] for item in listing.json()["sessions"]]


def test_delete_session_releases_ownership(monkeypatch):
    user = AuthenticatedUser(user_id="u_a", username="alice")
    ownership = FakeOwnership()
    client = build_app(user, monkeypatch, ownership)

    response = client.delete("/api/history/thread-a")

    assert response.status_code == 200
    assert ownership.deleted == ["thread-a"]
    assert ownership.released == ["thread-a"]
    assert ownership.get_owner("thread-a") is None
