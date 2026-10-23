from __future__ import annotations

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import StrEnum
from typing import Any, AsyncIterator, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agent.procurement.database import Database


class TaskEventType(StrEnum):
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    APPROVAL_REQUIRED = "approval.required"
    TASK_RESUMED = "task.resumed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TASK_HEARTBEAT = "task.heartbeat"


TERMINAL_EVENTS = {
    TaskEventType.TASK_COMPLETED,
    TaskEventType.TASK_FAILED,
    TaskEventType.TASK_CANCELLED,
}


class TaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    task_id: str
    sequence: int = Field(ge=1)
    type: TaskEventType
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 脱敏
# ============================================================
_SENSITIVE_KEY = re.compile(
    r"(token|password|passwd|secret|api[_-]?key|authorization|cookie|session)",
    re.IGNORECASE,
)
_MAX_STRING = 2000
_MAX_LIST = 50
_MAX_DEPTH = 5


def sanitize_event_data(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "<truncated>"
    if isinstance(value, str):
        return value[:_MAX_STRING]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                continue
            result[str(key)] = sanitize_event_data(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [
            sanitize_event_data(item, depth=depth + 1)
            for item in value[:_MAX_LIST]
        ]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_STRING]


# ============================================================
# 存储
# ============================================================
class TaskEventStore(Protocol):
    async def setup(self) -> None: ...

    async def append(
            self,
            *,
            task_id: str,
            event_type: TaskEventType,
            data: dict[str, Any],
            timestamp: datetime,
    ) -> TaskEvent: ...

    async def list_after(
            self,
            task_id: str,
            *,
            after_sequence: int,
            limit: int = 200,
    ) -> list[TaskEvent]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS procurement_task_event (
    event_id  TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL,
    sequence  BIGINT NOT NULL,
    type      TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    data      JSONB NOT NULL,
    UNIQUE (task_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_procurement_task_event_task
    ON procurement_task_event(task_id, sequence);
"""


class PostgresTaskEventStore:
    def __init__(self, database: Database):
        self._database = database

    async def setup(self) -> None:
        async with self._database.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def append(
            self,
            *,
            task_id: str,
            event_type: TaskEventType,
            data: dict[str, Any],
            timestamp: datetime,
    ) -> TaskEvent:
        await self.setup()
        event_id = f"evt-{uuid.uuid4().hex}"
        safe_data = sanitize_event_data(data)
        async with self._database.acquire() as conn:
            async with conn.transaction():
                # 同一个 task 的 sequence 在事务内串行分配。
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    task_id,
                )
                sequence = await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                      FROM procurement_task_event
                     WHERE task_id = $1
                    """,
                    task_id,
                )
                await conn.execute(
                    """
                    INSERT INTO procurement_task_event (
                        event_id, task_id, sequence, type, timestamp, data
                    ) VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                    """,
                    event_id,
                    task_id,
                    sequence,
                    event_type.value,
                    timestamp,
                    json.dumps(safe_data, ensure_ascii=False, default=str),
                )
        return TaskEvent(
            event_id=event_id,
            task_id=task_id,
            sequence=sequence,
            type=event_type,
            timestamp=timestamp,
            data=safe_data,
        )

    async def list_after(
            self,
            task_id: str,
            *,
            after_sequence: int,
            limit: int = 200,
    ) -> list[TaskEvent]:
        await self.setup()
        async with self._database.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM procurement_task_event
                 WHERE task_id = $1 AND sequence > $2
                 ORDER BY sequence ASC
                 LIMIT $3
                """,
                task_id,
                after_sequence,
                limit,
            )
        return [
            TaskEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                sequence=row["sequence"],
                type=row["type"],
                timestamp=row["timestamp"],
                data=_load_json(row["data"]),
            )
            for row in rows
        ]


def _load_json(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


# ============================================================
# Broker
# ============================================================
class TaskEventBroker(Protocol):
    @asynccontextmanager
    async def subscribe(
            self, task_id: str
    ) -> AsyncIterator[asyncio.Queue[TaskEvent]]: ...

    async def publish(self, event: TaskEvent) -> None: ...


class InMemoryTaskEventBroker:
    """单进程 Broker；适合本地开发和单 worker。"""

    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue[TaskEvent]]] = {}

    @asynccontextmanager
    async def subscribe(
            self, task_id: str
    ) -> AsyncIterator[asyncio.Queue[TaskEvent]]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            yield queue
        finally:
            queues = self._subscribers.get(task_id)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._subscribers.pop(task_id, None)

    async def publish(self, event: TaskEvent) -> None:
        for queue in list(self._subscribers.get(event.task_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


# ============================================================
# 事件服务
# ============================================================
class TaskEventService:
    def __init__(
            self,
            *,
            store: TaskEventStore,
            broker: TaskEventBroker,
            heartbeat_seconds: int = 15,
    ):
        self._store = store
        self._broker = broker
        self._heartbeat = heartbeat_seconds

    async def emit(
            self,
            *,
            task_id: str,
            event_type: TaskEventType,
            data: dict[str, Any] | None = None,
            timestamp: datetime | None = None,
    ) -> TaskEvent:
        event = await self._store.append(
            task_id=task_id,
            event_type=event_type,
            data=data or {},
            timestamp=timestamp or datetime.now().astimezone(),
        )
        await self._broker.publish(event)
        return event

    async def stream(
            self,
            *,
            task_id: str,
            after_sequence: int = 0,
    ) -> AsyncIterator[TaskEvent]:
        """先 replay，再消费 live；断线后从 after_sequence 继续。"""
        last = after_sequence
        async with self._broker.subscribe(task_id) as queue:
            # replay 分页，避免超过 limit 的事件被漏掉。
            while True:
                replay = await self._store.list_after(
                    task_id,
                    after_sequence=last,
                    limit=200,
                )
                if not replay:
                    break
                for event in replay:
                    if event.sequence <= last:
                        continue
                    last = event.sequence
                    yield event
                    if event.type in TERMINAL_EVENTS:
                        return
                if len(replay) < 200:
                    break

            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self._heartbeat
                    )
                except asyncio.TimeoutError:
                    yield TaskEvent(
                        event_id=f"evt-hb-{uuid.uuid4().hex}",
                        task_id=task_id,
                        sequence=max(last, 1),
                        type=TaskEventType.TASK_HEARTBEAT,
                        timestamp=datetime.now().astimezone(),
                        data={},
                    )
                    continue

                if event.sequence <= last:
                    continue
                last = event.sequence
                yield event
                if event.type in TERMINAL_EVENTS:
                    return


# ============================================================
# SSE 编码
# ============================================================
def encode_sse(event: TaskEvent) -> str:
    payload = event.model_dump(mode="json")
    # 心跳不带 id，避免覆盖浏览器自动维护的 Last-Event-ID。
    id_line = (
        ""
        if event.type == TaskEventType.TASK_HEARTBEAT
        else f"id: {event.sequence}\n"
    )
    return (
        f"{id_line}"
        f"event: {event.type.value}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
    )
