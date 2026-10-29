"""测试替身：最小 Redis 客户端。"""
from __future__ import annotations

import time
from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expires: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}

    def _alive(self, key: str) -> bool:
        deadline = self.expires.get(key)
        if deadline is not None and deadline <= time.monotonic():
            self.data.pop(key, None)
            self.expires.pop(key, None)
            return False
        return True

    def get(self, key: str) -> str | None:
        if not self._alive(key):
            return None
        return self.data.get(key)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and self._alive(key) and key in self.data:
            return None
        self.data[key] = value if isinstance(value, str) else str(value)
        if ex is not None:
            self.expires[key] = time.monotonic() + ex
        else:
            self.expires.pop(key, None)
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                removed += 1
            self.data.pop(key, None)
            self.expires.pop(key, None)
        return removed

    def incr(self, key: str) -> int:
        current = int(self.get(key) or 0) + 1
        self.data[key] = str(current)
        return current

    def expire(self, key: str, seconds: int) -> bool:
        if key not in self.data:
            return False
        self.expires[key] = time.monotonic() + seconds
        return True

    def ttl(self, key: str) -> int:
        if not self._alive(key):
            return -2
        deadline = self.expires.get(key)
        if deadline is None:
            return -1
        return max(0, int(deadline - time.monotonic()))

    def sadd(self, key: str, *values: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(str(value) for value in values)
        return len(bucket) - before

    def srem(self, key: str, *values: str) -> int:
        bucket = self.sets.get(key, set())
        removed = 0
        for value in values:
            if str(value) in bucket:
                bucket.remove(str(value))
                removed += 1
        return removed

    def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def ping(self) -> bool:
        return True

    def flushall(self) -> None:
        self.data.clear()
        self.expires.clear()
        self.sets.clear()
