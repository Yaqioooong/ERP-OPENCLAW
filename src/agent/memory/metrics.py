"""记忆子系统的进程内最小指标。

用于测试与本地观测；多实例部署应换成 Prometheus/Redis 聚合。
"""
from __future__ import annotations

from collections import Counter
from threading import Lock
from typing import Any


class MemoryMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[str] = Counter()

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None = None) -> str:
        if not labels:
            return name
        label_text = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
        return f"{name}{{{label_text}}}"

    def inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: int = 1,
    ) -> None:
        if value <= 0:
            return
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(sorted(self._counters.items()))
        return {"counters": counters}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


metrics = MemoryMetrics()
