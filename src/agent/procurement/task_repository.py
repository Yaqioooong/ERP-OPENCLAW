"""PostgreSQL 采购任务仓储。"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agent.procurement.database import Database
from agent.procurement.models import TaskRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS procurement_task (
    task_id        TEXT PRIMARY KEY,
    thread_id      TEXT NOT NULL UNIQUE,
    conversation_id TEXT,
    user_id        TEXT NOT NULL,
    status         TEXT NOT NULL,
    current_node   TEXT,
    retry_from     TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL,
    resumed_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_procurement_task_user_status
    ON procurement_task(user_id, status);

CREATE INDEX IF NOT EXISTS idx_procurement_task_status
    ON procurement_task(status, updated_at);
"""

_TERMINAL_STATUSES = {
    "completed",
    "rejected",
    "failed",
    "cancelled",
    "orphaned",
}
_RECOVERABLE_STATUSES = [
    "running",
    "parsing",
    "need_info",
    "analyzing",
    "plan_ready",
    "awaiting_approval",
    "awaiting_retry",
    "verifying",
    "approved_pending_execution",
]


class TaskRepository(Protocol):
    async def setup(self) -> None: ...

    async def create(self, record: TaskRecord) -> None: ...

    async def get(self, task_id: str) -> TaskRecord | None: ...

    async def get_by_thread(self, thread_id: str) -> TaskRecord | None: ...

    async def update_status(
        self,
        *,
        task_id: str,
        status: str,
        current_node: str | None = None,
        retry_from: str | None = None,
        error: dict | None = None,
        now: datetime | None = None,
    ) -> None: ...

    async def mark_resumed(self, task_id: str, now: datetime) -> None: ...

    async def list_recoverable(self, *, limit: int = 200) -> list[TaskRecord]: ...

    async def list_by_user(self, user_id: str, *, limit: int = 50) -> list[TaskRecord]: ...


class PostgresTaskRepository:
    def __init__(self, database: Database):
        self._database = database

    async def setup(self) -> None:
        async with self._database.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def create(self, record: TaskRecord) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO procurement_task (
                    task_id, thread_id, conversation_id, user_id, status,
                    current_node, retry_from, last_error_code, last_error_message,
                    created_at, updated_at, resumed_at, completed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                record.task_id,
                record.thread_id,
                record.conversation_id,
                record.user_id,
                record.status,
                record.current_node,
                record.retry_from,
                record.last_error_code,
                record.last_error_message,
                record.created_at,
                record.updated_at,
                record.resumed_at,
                record.completed_at,
            )

    async def get(self, task_id: str) -> TaskRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM procurement_task WHERE task_id = $1",
                task_id,
            )
        return TaskRecord(**dict(row)) if row else None

    async def get_by_thread(self, thread_id: str) -> TaskRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM procurement_task WHERE thread_id = $1",
                thread_id,
            )
        return TaskRecord(**dict(row)) if row else None

    async def update_status(
        self,
        *,
        task_id: str,
        status: str,
        current_node: str | None = None,
        retry_from: str | None = None,
        error: dict | None = None,
        now: datetime | None = None,
    ) -> None:
        await self.setup()
        timestamp = now or datetime.now().astimezone()
        error_code = error.get("code") if error else None
        error_message = error.get("message") if error else None
        completed_at = timestamp if status in _TERMINAL_STATUSES else None

        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_task
                   SET status = $1,
                       current_node = COALESCE($2, current_node),
                       retry_from = $3,
                       last_error_code = $4,
                       last_error_message = $5,
                       updated_at = $6,
                       completed_at = COALESCE($7, completed_at)
                 WHERE task_id = $8
                """,
                status,
                current_node,
                retry_from,
                error_code,
                error_message,
                timestamp,
                completed_at,
                task_id,
            )

    async def mark_resumed(self, task_id: str, now: datetime) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_task
                   SET resumed_at = $1, updated_at = $2
                 WHERE task_id = $3
                """,
                now,
                now,
                task_id,
            )

    async def list_recoverable(self, *, limit: int = 200) -> list[TaskRecord]:
        await self.setup()
        async with self._database.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM procurement_task
                 WHERE status = ANY($1::text[])
                 ORDER BY updated_at DESC
                 LIMIT $2
                """,
                _RECOVERABLE_STATUSES,
                limit,
            )
        return [TaskRecord(**dict(row)) for row in rows]

    async def list_by_user(self, user_id: str, *, limit: int = 50) -> list[TaskRecord]:
        await self.setup()
        async with self._database.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM procurement_task
                 WHERE user_id = $1
                 ORDER BY updated_at DESC
                 LIMIT $2
                """,
                user_id,
                limit,
            )
        return [TaskRecord(**dict(row)) for row in rows]
