from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_view.api import procurement
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        return {"task_id": "TASK-1", "status": "running"}

    async def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"task_id": kwargs["task_id"], "status": "running"}


def build_app(user: AuthenticatedUser, monkeypatch):
    runner = FakeRunner()

    async def get_runner():
        return runner

    monkeypatch.setattr(procurement, "get_task_runner", get_runner)
    app = FastAPI()
    app.include_router(procurement.router, prefix="/api")
    app.dependency_overrides[require_password_ready] = lambda: user
    return TestClient(app), runner


def test_start_task_uses_session_identity_not_headers(monkeypatch):
    user = AuthenticatedUser(
        user_id="u_a", username="alice", roles=("requester",)
    )
    client, runner = build_app(user, monkeypatch)
    response = client.post(
        "/api/procurement/tasks",
        json={"message": "采购刹车片"},
        headers={"X-User-Id": "attacker", "X-Roles": "admin"},
    )
    assert response.status_code == 200
    _, kwargs = runner.calls[0]
    assert kwargs["user_id"] == "u_a"
    assert kwargs["username"] == "alice"
    assert kwargs["roles"] == ("requester",)


def test_get_task_passes_db_roles(monkeypatch):
    user = AuthenticatedUser(
        user_id="u_a", username="alice", roles=("requester", "approver")
    )
    client, runner = build_app(user, monkeypatch)
    response = client.get("/api/procurement/tasks/TASK-1")
    assert response.status_code == 200
    _, kwargs = runner.calls[0]
    assert kwargs == {
        "task_id": "TASK-1",
        "user_id": "u_a",
        "roles": ("requester", "approver"),
    }


def test_procurement_source_has_no_client_identity_fallback():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "src/api_view/api/procurement.py"
    ).read_text(encoding="utf-8")
    assert "X-User-Id" not in source
    assert "X-Roles" not in source
    assert "guoyaxing" not in source


def test_frontend_procurement_uses_cookie_not_identity_headers():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "frontend/src/api/procurement.js"
    ).read_text(encoding="utf-8")
    assert "X-User-Id" not in source
    assert "X-Roles" not in source
    assert "credentials: 'same-origin'" in source
