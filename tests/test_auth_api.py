from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_view.auth import config as auth_config
from api_view.auth.models import AuthenticatedUser
from api_view.auth.passwords import hash_password
from api_view.auth.router import router
from api_view.auth.sessions import (
    LoginRateLimiter,
    SessionStore,
    get_login_limiter,
    get_session_store,
)
from api_view.auth.users import UserRecord, get_user_repository
from tests.fakes import FakeRedis


class FakeUsers:
    def __init__(self, records):
        self.records = {record.username.lower(): record for record in records}

    async def get_by_username(self, username: str):
        return self.records.get(username.lower())

    async def get(self, user_id: str):
        for record in self.records.values():
            if record.user_id == user_id:
                return record
        return None

    async def mark_login(self, user_id: str):
        return None

    async def create(self, **kwargs):
        record = UserRecord(
            user_id=kwargs["user_id"],
            username=kwargs["username"],
            password_hash=kwargs["password_hash"],
            roles=tuple(kwargs.get("roles", ("requester",))),
            is_active=True,
            display_name=kwargs.get("display_name", ""),
        )
        self.records[record.username.lower()] = record
        return record


def make_record(
    *,
    user_id: str = "u_1001",
    username: str = "zhangsan",
    password: str = "password-123",
    active: bool = True,
) -> UserRecord:
    return UserRecord(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        roles=("requester",),
        is_active=active,
    )


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setattr(auth_config, "COOKIE_SECURE", False)
    monkeypatch.setattr(auth_config, "IS_PRODUCTION", False)
    monkeypatch.setattr(auth_config, "DEV_DEMO_USER_ID", "")
    monkeypatch.setattr(auth_config, "LOGIN_MAX_FAILURES", 10)

    redis = FakeRedis()
    users = FakeUsers([make_record()])
    sessions = SessionStore(redis, ttl_seconds=600)
    limiter = LoginRateLimiter(redis, max_failures=10, window_seconds=60)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    return TestClient(app), users, sessions, limiter


def test_login_me_logout(auth_app):
    client, _, _, _ = auth_app
    response = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "password-123"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["user_id"] == "u_1001"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "erp_session=" in cookie

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "zhangsan"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_login_wrong_password_and_unknown_user(auth_app):
    client, _, _, _ = auth_app
    wrong = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert "用户名或密码错误" in wrong.json()["detail"]
    assert "erp_session" not in wrong.headers.get("set-cookie", "")

    unknown = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "password-123"},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "用户名或密码错误"


def test_inactive_user_cannot_login():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    sessions = SessionStore(FakeRedis(), ttl_seconds=600)
    limiter = LoginRateLimiter(FakeRedis(), max_failures=10, window_seconds=60)
    app.dependency_overrides[get_user_repository] = lambda: FakeUsers(
        [make_record(active=False)]
    )
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    response = TestClient(app).post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "password-123"},
    )
    assert response.status_code == 401


def test_login_rate_limited(auth_app):
    client, _, _, limiter = auth_app
    limiter._max = 2
    for _ in range(2):
        response = client.post(
            "/api/auth/login",
            json={"username": "zhangsan", "password": "wrong-password"},
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "wrong-password"},
    )
    assert blocked.status_code == 429


def test_origin_whitelist(auth_app):
    client, _, _, _ = auth_app
    rejected = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "password-123"},
        headers={"Origin": "http://evil.example"},
    )
    assert rejected.status_code == 403

    allowed = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "password-123"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert allowed.status_code == 200


def test_dev_login_disabled_outside_explicit_dev_config(auth_app, monkeypatch):
    client, _, _, _ = auth_app
    assert client.post("/api/auth/dev-login").status_code == 404

    monkeypatch.setattr(auth_config, "DEV_DEMO_USER_ID", "u_demo")
    monkeypatch.setattr(auth_config, "DEV_DEMO_USERNAME", "demo")
    response = client.post("/api/auth/dev-login")
    assert response.status_code == 200
    assert response.json()["user"]["user_id"] == "u_demo"

    monkeypatch.setattr(auth_config, "IS_PRODUCTION", True)
    assert client.post("/api/auth/dev-login").status_code == 404


def test_me_without_cookie_is_401(auth_app):
    client, _, _, _ = auth_app
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Cookie"


def test_login_does_not_leak_demo_identity(auth_app):
    client, _, _, _ = auth_app
    response = client.post(
        "/api/auth/login",
        json={"username": "zhangsan", "password": "password-123"},
    )
    user = response.json()["user"]
    assert user["user_id"] == "u_1001"
    assert "password_hash" not in str(user).lower()
    assert user["roles"] == ["requester"]
    assert AuthenticatedUser(**user).user_id == "u_1001"
