from __future__ import annotations

from fastapi.testclient import TestClient

from api_view.auth import config as auth_config
from tests.auth_support import InMemoryUsers, build_auth_app, make_record


def setup(monkeypatch):
    monkeypatch.setattr(auth_config, "COOKIE_SECURE", False)
    users = InMemoryUsers(
        [
            make_record(user_id="u_admin", username="admin", roles=("admin",)),
            make_record(user_id="u_user", username="user", roles=("requester",)),
        ]
    )
    app, _, _, _ = build_auth_app(users)
    return users, TestClient(app), TestClient(app)


def test_requester_cannot_access_admin_endpoints(monkeypatch):
    users, admin_client, user_client = setup(monkeypatch)
    assert user_client.post(
        "/api/auth/login", json={"username": "user", "password": "password-123"}
    ).status_code == 200
    assert user_client.get("/api/admin/users").status_code == 403
    assert user_client.post(
        "/api/admin/users/u_admin/reset-password"
    ).status_code == 403


def test_admin_lists_and_updates_user(monkeypatch):
    users, admin_client, user_client = setup(monkeypatch)
    assert admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    ).status_code == 200

    listing = admin_client.get("/api/admin/users?query=user")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["user_id"] == "u_user"

    updated = admin_client.patch(
        "/api/admin/users/u_user",
        json={"roles": ["requester", "approver"], "display_name": "采购员"},
    )
    assert updated.status_code == 200
    assert "approver" in updated.json()["user"]["roles"]
    assert updated.json()["user"]["display_name"] == "采购员"

    assert user_client.post(
        "/api/auth/login", json={"username": "user", "password": "password-123"}
    ).status_code == 200
    disabled = admin_client.patch(
        "/api/admin/users/u_user", json={"is_active": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["user"]["is_active"] is False
    assert user_client.get("/api/auth/me").status_code == 401


def test_admin_cannot_modify_self(monkeypatch):
    _, admin_client, _ = setup(monkeypatch)
    assert admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    ).status_code == 200
    response = admin_client.patch(
        "/api/admin/users/u_admin", json={"roles": ["requester"]}
    )
    assert response.status_code == 409


def test_admin_reset_password_forces_change(monkeypatch):
    _, admin_client, user_client = setup(monkeypatch)
    assert user_client.post(
        "/api/auth/login", json={"username": "user", "password": "password-123"}
    ).status_code == 200
    assert admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    ).status_code == 200

    reset = admin_client.post("/api/admin/users/u_user/reset-password")
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert temporary

    # 旧会话已被吊销
    assert user_client.get("/api/auth/me").status_code == 401

    relogin = user_client.post(
        "/api/auth/login", json={"username": "user", "password": temporary}
    )
    assert relogin.status_code == 200
    assert relogin.json()["must_change_password"] is True
    blocked = user_client.post("/api/auth/role-requests", json={"role": "approver"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "password_change_required"

    changed = user_client.post(
        "/api/auth/change-password",
        json={
            "current_password": temporary,
            "new_password": "BrandNew123",
            "confirm_password": "BrandNew123",
        },
    )
    assert changed.status_code == 200
    assert user_client.post(
        "/api/auth/role-requests", json={"role": "approver"}
    ).status_code == 201


def test_role_request_flow_and_immediate_effect(monkeypatch):
    users, admin_client, user_client = setup(monkeypatch)
    assert user_client.post(
        "/api/auth/login", json={"username": "user", "password": "password-123"}
    ).status_code == 200
    assert admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    ).status_code == 200

    created = user_client.post(
        "/api/auth/role-requests",
        json={"role": "approver", "reason": "需要审批采购单"},
    )
    assert created.status_code == 201
    request_id = created.json()["request"]["request_id"]

    duplicate = user_client.post(
        "/api/auth/role-requests", json={"role": "approver"}
    )
    assert duplicate.status_code == 409

    pending = admin_client.get("/api/admin/role-requests?status=pending")
    assert pending.status_code == 200
    assert any(r["request_id"] == request_id for r in pending.json()["requests"])

    approved = admin_client.post(
        f"/api/admin/role-requests/{request_id}/approve", json={"comment": "通过"}
    )
    assert approved.status_code == 200
    assert approved.json()["request"]["status"] == "approved"

    # 不重新登录，角色也应立即生效
    me = user_client.get("/api/auth/me")
    assert "approver" in me.json()["user"]["roles"]
    assert user_client.post(
        "/api/auth/role-requests", json={"role": "approver"}
    ).status_code == 409


def test_role_request_reject(monkeypatch):
    _, admin_client, user_client = setup(monkeypatch)
    assert user_client.post(
        "/api/auth/login", json={"username": "user", "password": "password-123"}
    ).status_code == 200
    assert admin_client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    ).status_code == 200
    request_id = user_client.post(
        "/api/auth/role-requests", json={"role": "approver"}
    ).json()["request"]["request_id"]

    rejected = admin_client.post(
        f"/api/admin/role-requests/{request_id}/reject", json={"comment": "不通过"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["request"]["status"] == "rejected"
    assert "approver" not in user_client.get("/api/auth/me").json()["user"]["roles"]
