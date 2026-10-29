from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_view.auth import config as auth_config
from tests.auth_support import InMemoryUsers, build_auth_app, make_record


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setattr(auth_config, "COOKIE_SECURE", False)
    users = InMemoryUsers([make_record()])
    app, sessions, _, register_limiter = build_auth_app(users)
    return TestClient(app), users, sessions, register_limiter


def test_register_success_auto_login(auth_env):
    client, users, _, _ = auth_env
    response = client.post(
        "/api/auth/register",
        json={
            "username": "newuser",
            "display_name": "新用户",
            "password": "GoodPass123",
            "confirm_password": "GoodPass123",
        },
    )
    assert response.status_code == 201
    user = response.json()["user"]
    assert user["username"] == "newuser"
    assert user["roles"] == ["requester"]
    assert user["display_name"] == "新用户"
    assert user["user_id"].startswith("u_")
    assert "erp_session=" in response.headers["set-cookie"]

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["user_id"] == user["user_id"]


def test_register_duplicate_username_is_case_insensitive(auth_env):
    client, _, _, _ = auth_env
    response = client.post(
        "/api/auth/register",
        json={
            "username": "ZhangSan",
            "display_name": "重复",
            "password": "GoodPass123",
            "confirm_password": "GoodPass123",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_taken"


def test_register_validation_errors_are_field_scoped(auth_env):
    client, _, _, _ = auth_env
    response = client.post(
        "/api/auth/register",
        json={
            "username": "validuser",
            "display_name": "",
            "password": "short",
            "confirm_password": "different",
        },
    )
    assert response.status_code == 422
    errors = response.json()["detail"]["field_errors"]
    assert "display_name" in errors
    assert "password" in errors


def test_register_password_confirmation(auth_env):
    client, _, _, _ = auth_env
    response = client.post(
        "/api/auth/register",
        json={
            "username": "newuser2",
            "display_name": "新用户",
            "password": "GoodPass123",
            "confirm_password": "GoodPass124",
        },
    )
    assert response.status_code == 422
    assert "confirm_password" in response.json()["detail"]["field_errors"]


def test_register_rate_limited(auth_env):
    client, _, _, limiter = auth_env
    limiter._max = 1
    first = client.post(
        "/api/auth/register",
        json={
            "username": "user_one",
            "display_name": "用户一",
            "password": "GoodPass123",
            "confirm_password": "GoodPass123",
        },
    )
    assert first.status_code == 201
    second = client.post(
        "/api/auth/register",
        json={
            "username": "user_two",
            "display_name": "用户二",
            "password": "GoodPass123",
            "confirm_password": "GoodPass123",
        },
    )
    assert second.status_code == 429


def test_auth_policy_is_public(auth_env):
    client, _, _, _ = auth_env
    response = client.get("/api/auth/policy")
    assert response.status_code == 200
    assert response.json()["registration_enabled"] is True
    assert response.json()["password"]["min"] == 8


def test_auth_logs_do_not_contain_passwords(auth_env):
    from loguru import logger

    client, _, _, _ = auth_env
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    try:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "loguser",
                "display_name": "日志用户",
                "password": "VerySecret123",
                "confirm_password": "VerySecret123",
            },
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 201
    joined = "\n".join(messages)
    assert "auth_event" in joined
    assert "VerySecret123" not in joined
