from __future__ import annotations

import time

import pytest

from agent.memory.preference_lock import (
    PreferenceLockUnavailable,
    RedisPreferenceLock,
)


class LockRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expires: dict[str, float] = {}
        self.fail = False

    def _alive(self, key):
        deadline = self.expires.get(key)
        if deadline is not None and deadline <= time.monotonic():
            self.data.pop(key, None)
            self.expires.pop(key, None)
            return False
        return True

    def set(self, key, value, nx=False, px=None):
        if self.fail:
            raise RuntimeError("redis down")
        if nx and self._alive(key) and key in self.data:
            return None
        self.data[key] = value
        if px is not None:
            self.expires[key] = time.monotonic() + px / 1000
        return True

    def eval(self, script, numkeys, key, token):
        if self._alive(key) and self.data.get(key) == token:
            self.data.pop(key, None)
            self.expires.pop(key, None)
            return 1
        return 0


@pytest.mark.anyio
async def test_lock_acquire_release_and_contention():
    redis = LockRedis()
    lock = RedisPreferenceLock(redis, retry_ms=5)

    token = await lock.acquire("k", ttl_ms=1000, wait_ms=0)
    assert token
    assert await lock.acquire("k", ttl_ms=1000, wait_ms=0) is None

    await lock.release("k", token)
    second = await lock.acquire("k", ttl_ms=1000, wait_ms=0)
    assert second


@pytest.mark.anyio
async def test_wrong_token_does_not_release_other_lock():
    redis = LockRedis()
    lock = RedisPreferenceLock(redis, retry_ms=5)
    token = await lock.acquire("k", ttl_ms=1000, wait_ms=0)
    await lock.release("k", "wrong-token")
    assert redis.data["k"] == token
    await lock.release("k", token)
    assert "k" not in redis.data


@pytest.mark.anyio
async def test_expired_lock_can_be_acquired():
    redis = LockRedis()
    lock = RedisPreferenceLock(redis, retry_ms=5)
    await lock.acquire("k", ttl_ms=1, wait_ms=0)
    await __import__("asyncio").sleep(0.01)
    token = await lock.acquire("k", ttl_ms=1000, wait_ms=0)
    assert token


@pytest.mark.anyio
async def test_redis_error_raises_unavailable():
    redis = LockRedis()
    redis.fail = True
    lock = RedisPreferenceLock(redis, retry_ms=5)
    with pytest.raises(PreferenceLockUnavailable):
        await lock.acquire("k", ttl_ms=1000, wait_ms=0)
