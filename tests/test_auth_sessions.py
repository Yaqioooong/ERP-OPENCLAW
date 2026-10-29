from __future__ import annotations

import json

from api_view.auth.models import AuthenticatedUser
from api_view.auth.sessions import LoginRateLimiter, SessionStore
from tests.fakes import FakeRedis


def make_user(user_id: str = "u_1001") -> AuthenticatedUser:
    return AuthenticatedUser(user_id=user_id, username="zhangsan", roles=("requester",))


def test_session_roundtrip_and_revoke():
    client = FakeRedis()
    store = SessionStore(client, ttl_seconds=60)

    token = store.create(make_user())
    assert token
    assert token not in client.data  # 只存哈希
    assert store.resolve(token) == make_user()

    store.revoke(token)
    assert store.resolve(token) is None


def test_session_expiry():
    client = FakeRedis()
    store = SessionStore(client, ttl_seconds=1)
    token = store.create(make_user())
    for key in list(client.expires):
        client.expires[key] = 0
    assert store.resolve(token) is None


def test_malformed_session_is_removed():
    client = FakeRedis()
    store = SessionStore(client, ttl_seconds=60)
    token = store.create(make_user())
    key = next(iter(client.data))
    client.data[key] = json.dumps({"username": "broken"})
    assert store.resolve(token) is None
    assert key not in client.data


def test_login_rate_limiter_blocks_after_threshold():
    limiter = LoginRateLimiter(FakeRedis(), max_failures=2, window_seconds=60)
    assert not limiter.is_blocked("zhangsan", "127.0.0.1")
    limiter.record_failure("zhangsan", "127.0.0.1")
    assert not limiter.is_blocked("zhangsan", "127.0.0.1")
    limiter.record_failure("zhangsan", "127.0.0.1")
    assert limiter.is_blocked("zhangsan", "127.0.0.1")
    limiter.reset("zhangsan", "127.0.0.1")
    assert not limiter.is_blocked("zhangsan", "127.0.0.1")
