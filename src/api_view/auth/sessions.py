"""服务端会话与登录失败限流（Redis）。"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

import redis

from api_view.auth import config
from api_view.auth.models import AuthenticatedUser

SESSION_PREFIX = "erp:auth:session:"
FAIL_PREFIX = "erp:auth:fail:"
USER_SESSIONS_PREFIX = "erp:auth:user_sessions:"
REGISTER_PREFIX = "erp:auth:register:"


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class SessionStore:
    """不透明 token 会话；Redis 只保存 token 的 SHA-256。"""

    def __init__(self, client: redis.Redis, ttl_seconds: int | None = None):
        self._client = client
        self._ttl = ttl_seconds or config.SESSION_TTL_SECONDS

    @staticmethod
    def _key(token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{SESSION_PREFIX}{digest}"

    def create(self, user: AuthenticatedUser) -> str:
        token = secrets.token_urlsafe(32)
        key = self._key(token)
        payload = json.dumps(
            {
                "user_id": user.user_id,
                "username": user.username,
                "display_name": user.display_name,
                "roles": list(user.roles),
                "must_change_password": user.must_change_password,
            },
            ensure_ascii=False,
        )
        self._client.set(key, payload, ex=self._ttl)
        try:
            index = f"{USER_SESSIONS_PREFIX}{user.user_id}"
            self._client.sadd(index, key)
            self._client.expire(index, self._ttl)
        except Exception:
            pass
        return token

    def resolve(self, token: str | None) -> AuthenticatedUser | None:
        if not token:
            return None
        try:
            raw = self._client.get(self._key(token))
        except Exception:
            return None
        if raw is None:
            return None
        try:
            data = json.loads(_as_text(raw))
            return AuthenticatedUser(
                user_id=str(data["user_id"]),
                username=str(data.get("username") or data["user_id"]),
                roles=tuple(str(role) for role in (data.get("roles") or ())),
                display_name=str(data.get("display_name") or ""),
                must_change_password=bool(data.get("must_change_password")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.revoke(token)
            return None

    def revoke(self, token: str | None) -> None:
        if token:
            try:
                self._client.delete(self._key(token))
            except Exception:
                pass

    def revoke_all(self, user_id: str) -> None:
        """吊销某用户的全部会话（禁用、重置密码、退出所有设备）。"""
        index = f"{USER_SESSIONS_PREFIX}{user_id}"
        try:
            keys = self._client.smembers(index) or set()
            if keys:
                self._client.delete(*[str(key) for key in keys])
            self._client.delete(index)
        except Exception:
            pass


class RegistrationLimiter:
    """按客户端 IP 限制注册频率，防止批量注册。"""

    def __init__(
        self,
        client: redis.Redis,
        *,
        max_attempts: int | None = None,
        window_seconds: int | None = None,
    ):
        self._client = client
        self._max = max_attempts or config.REGISTER_MAX_ATTEMPTS
        self._window = window_seconds or config.REGISTER_WINDOW_SECONDS

    def is_blocked(self, client_ip: str) -> bool:
        try:
            value = self._client.get(f"{REGISTER_PREFIX}{client_ip}")
            return int(_as_text(value)) >= self._max if value is not None else False
        except Exception:
            return False

    def record(self, client_ip: str) -> None:
        key = f"{REGISTER_PREFIX}{client_ip}"
        try:
            count = int(self._client.incr(key))
            if count == 1:
                self._client.expire(key, self._window)
        except Exception:
            pass


class LoginRateLimiter:
    """按 username + client_ip 统计失败次数，窗口内超限返回 429。"""

    def __init__(
        self,
        client: redis.Redis,
        *,
        max_failures: int | None = None,
        window_seconds: int | None = None,
    ):
        self._client = client
        self._max = max_failures or config.LOGIN_MAX_FAILURES
        self._window = window_seconds or config.LOGIN_WINDOW_SECONDS

    def _key(self, username: str, client_ip: str) -> str:
        return f"{FAIL_PREFIX}{username.strip().lower()}:{client_ip}"

    def is_blocked(self, username: str, client_ip: str) -> bool:
        try:
            value = self._client.get(self._key(username, client_ip))
            return int(_as_text(value)) >= self._max if value is not None else False
        except Exception:
            return False

    def record_failure(self, username: str, client_ip: str) -> None:
        key = self._key(username, client_ip)
        try:
            count = int(self._client.incr(key))
            if count == 1:
                self._client.expire(key, self._window)
        except Exception:
            pass

    def reset(self, username: str, client_ip: str) -> None:
        try:
            self._client.delete(self._key(username, client_ip))
        except Exception:
            pass


_client: redis.Redis | None = None
_session_store: SessionStore | None = None
_login_limiter: LoginRateLimiter | None = None
_registration_limiter: RegistrationLimiter | None = None


def get_redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(config.AUTH_REDIS_URL, decode_responses=True)
    return _client


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore(get_redis_client())
    return _session_store


def get_login_limiter() -> LoginRateLimiter:
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = LoginRateLimiter(get_redis_client())
    return _login_limiter


def get_registration_limiter() -> RegistrationLimiter:
    global _registration_limiter
    if _registration_limiter is None:
        _registration_limiter = RegistrationLimiter(get_redis_client())
    return _registration_limiter
