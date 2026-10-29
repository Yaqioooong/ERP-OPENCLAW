from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_view.auth import config as auth_config
from tests.auth_support import InMemoryUsers, build_auth_app, login, make_record


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setattr(auth_config, "COOKIE_SECURE", False)
    users = InMemoryUsers([make_record()])
    app, sessions, _, _ = build_auth_app(users)
    return TestClient(app), users, sessions


def test_change_password_success(auth_env):
    client, users, _ = auth_env
    assert login(client).status_code == 200
    response = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "password-123",
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["must_change_password"] is False
    assert client.get("/api/auth/me").status_code == 200
    assert users.by_id["u_1001"].must_change_password is False


def test_change_password_wrong_current(auth_env):
    client, _, _ = auth_env
    assert login(client).status_code == 200
    response = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "wrong-password",
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
    )
    assert response.status_code == 400


def test_change_password_policy_and_same_password(auth_env):
    client, _, _ = auth_env
    assert login(client).status_code == 200

    weak = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "password-123",
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert weak.status_code == 422
    assert "password" in weak.json()["detail"]["field_errors"]

    same = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "password-123",
            "new_password": "password-123",
            "confirm_password": "password-123",
        },
    )
    assert same.status_code == 400


def test_force_password_change_blocks_business_endpoints(monkeypatch):
    monkeypatch.setattr(auth_config, "COOKIE_SECURE", False)
    users = InMemoryUsers([make_record(must_change_password=True)])
    app, _, _, _ = build_auth_app(users)
    client = TestClient(app)

    assert login(client).status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    blocked = client.post("/api/auth/role-requests", json={"role": "approver"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "password_change_required"

    changed = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "password-123",
            "new_password": "newpass123",
            "confirm_password": "newpass123",
        },
    )
    assert changed.status_code == 200
    allowed = client.post("/api/auth/role-requests", json={"role": "approver"})
    assert allowed.status_code == 201


def test_logout_all_revokes_session(auth_env):
    client, _, _ = auth_env
    assert login(client).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout-all").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_disabled_user_session_is_invalid_immediately(auth_env):
    client, users, _ = auth_env
    assert login(client).status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    import asyncio

    asyncio.run(users.set_active("u_1001", False))
    assert client.get("/api/auth/me").status_code == 401


def test_role_change_takes_effect_without_relogin(auth_env):
    client, users, _ = auth_env
    assert login(client).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["roles"] == ["requester"]

    import asyncio

    asyncio.run(users.set_roles("u_1001", ("requester", "approver")))
    assert client.get("/api/auth/me").json()["user"]["roles"] == [
        "requester",
        "approver",
    ]
