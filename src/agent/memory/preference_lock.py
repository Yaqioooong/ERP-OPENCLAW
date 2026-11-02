"""偏好提交用的 Redis 互斥锁。

锁是跨实例的：所有应用实例必须共享同一个 Redis。释放时用 Lua 校验 token，
避免锁过期后被其他提交持有而误删。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Protocol

from loguru import logger

PREFERENCE_LOCK_TTL_MS = int(os.getenv("PREFERENCE_LOCK_TTL_MS", "10000"))
PREFERENCE_LOCK_WAIT_MS = int(os.getenv("PREFERENCE_LOCK_WAIT_MS", "2000"))
PREFERENCE_LOCK_RETRY_MS = int(os.getenv("PREFERENCE_LOCK_RETRY_MS", "50"))

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class PreferenceLockUnavailable(RuntimeError):
    """Redis 不可用，调用方必须失败关闭。"""


class PreferenceLock(Protocol):
    async def acquire(
        self,
        key: str,
        *,
        ttl_ms: int | None = None,
        wait_ms: int | None = None,
    ) -> str | None: ...

    async def release(self, key: str, token: str) -> None: ...


def lock_key(user_id: str) -> str:
    return f"erp:memory:lock:{user_id}:preferences"


async def _call(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


class RedisPreferenceLock:
    def __init__(self, client: Any, *, retry_ms: int | None = None):
        self._client = client
        self._retry_ms = retry_ms or PREFERENCE_LOCK_RETRY_MS

    async def acquire(
        self,
        key: str,
        *,
        ttl_ms: int | None = None,
        wait_ms: int | None = None,
    ) -> str | None:
        token = uuid.uuid4().hex
        ttl = ttl_ms or PREFERENCE_LOCK_TTL_MS
        wait = wait_ms if wait_ms is not None else PREFERENCE_LOCK_WAIT_MS
        deadline = time.monotonic() + max(0, wait) / 1000.0

        while True:
            try:
                acquired = await _call(
                    self._client.set,
                    key,
                    token,
                    nx=True,
                    px=int(ttl),
                )
            except Exception as exc:
                raise PreferenceLockUnavailable("偏好锁存储不可用") from exc
            if acquired:
                return token
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(self._retry_ms / 1000.0)

    async def release(self, key: str, token: str) -> None:
        try:
            await _call(self._client.eval, _RELEASE_SCRIPT, 1, key, token)
        except Exception:
            # 释放失败不应覆盖提交结果；锁会按 TTL 自动释放。
            logger.warning("PreferenceLock: release failed key={}", key)


class InMemoryPreferenceLock:
    """测试/单进程用锁；生产必须使用 Redis 实现。"""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(
        self,
        key: str,
        *,
        ttl_ms: int | None = None,
        wait_ms: int | None = None,
    ) -> str | None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        timeout = None if wait_ms is None else max(0, wait_ms) / 1000.0
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return "in-memory"

    async def release(self, key: str, token: str) -> None:
        lock = self._locks.get(key)
        if lock is not None and lock.locked():
            lock.release()
