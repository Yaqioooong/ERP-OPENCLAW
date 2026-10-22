"""采购任务的业务事实仓储。

Checkpoint 只保存图执行快照；任务归属、状态、当前节点、错误等信息由这里持久化，
用于重启后的查询、鉴权和对账。
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Protocol

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
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    resumed_at     TEXT,
    completed_at   TEXT
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
_RECOVERABLE_STATUSES = (
    "running",
    "parsing",
    "need_info",
    "analyzing",
    "plan_ready",
    "awaiting_approval",
    "awaiting_retry",
    "verifying",
    "approved_pending_execution",
)


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


class SqliteTaskRepository:
    """SQLite 实现；与审批记录共用同一个业务库文件。"""

    def __init__(self, path: str | Path):
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def setup(self) -> None:
        await asyncio.to_thread(self._setup_sync)

    def _setup_sync(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    async def create(self, record: TaskRecord) -> None:
        await asyncio.to_thread(self._create_sync, record)

    def _create_sync(self, record: TaskRecord) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT INTO procurement_task (
                    task_id, thread_id, conversation_id, user_id, status,
                    current_node, retry_from, last_error_code, last_error_message,
                    created_at, updated_at, resumed_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.thread_id,
                    record.conversation_id,
                    record.user_id,
                    record.status,
                    record.current_node,
                    record.retry_from,
                    record.last_error_code,
                    record.last_error_message,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.resumed_at.isoformat() if record.resumed_at else None,
                    record.completed_at.isoformat() if record.completed_at else None,
                ),
            )
            conn.commit()

    async def get(self, task_id: str) -> TaskRecord | None:
        return await asyncio.to_thread(self._get_sync, task_id)

    def _get_sync(self, task_id: str) -> TaskRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM procurement_task WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    async def get_by_thread(self, thread_id: str) -> TaskRecord | None:
        return await asyncio.to_thread(self._get_by_thread_sync, thread_id)

    def _get_by_thread_sync(self, thread_id: str) -> TaskRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM procurement_task WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

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
        await asyncio.to_thread(
            self._update_status_sync,
            task_id,
            status,
            current_node,
            retry_from,
            error,
            now,
        )

    def _update_status_sync(
        self,
        task_id: str,
        status: str,
        current_node: str | None,
        retry_from: str | None,
        error: dict | None,
        now: datetime | None,
    ) -> None:
        timestamp = now or datetime.now().astimezone()
        error_code = error.get("code") if error else None
        error_message = error.get("message") if error else None
        completed_at = timestamp.isoformat() if status in _TERMINAL_STATUSES else None

        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                UPDATE procurement_task
                   SET status = ?,
                       current_node = COALESCE(?, current_node),
                       retry_from = ?,
                       last_error_code = ?,
                       last_error_message = ?,
                       updated_at = ?,
                       completed_at = COALESCE(?, completed_at)
                 WHERE task_id = ?
                """,
                (
                    status,
                    current_node,
                    retry_from,
                    error_code,
                    error_message,
                    timestamp.isoformat(),
                    completed_at,
                    task_id,
                ),
            )
            conn.commit()

    async def mark_resumed(self, task_id: str, now: datetime) -> None:
        await asyncio.to_thread(self._mark_resumed_sync, task_id, now)

    def _mark_resumed_sync(self, task_id: str, now: datetime) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                UPDATE procurement_task
                   SET resumed_at = ?, updated_at = ?
                 WHERE task_id = ?
                """,
                (now.isoformat(), now.isoformat(), task_id),
            )
            conn.commit()

    async def list_recoverable(self, *, limit: int = 200) -> list[TaskRecord]:
        return await asyncio.to_thread(self._list_recoverable_sync, limit)

    def _list_recoverable_sync(self, limit: int) -> list[TaskRecord]:
        placeholders = ",".join("?" for _ in _RECOVERABLE_STATUSES)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            rows = conn.execute(
                f"""
                SELECT * FROM procurement_task
                 WHERE status IN ({placeholders})
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (*_RECOVERABLE_STATUSES, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    async def list_by_user(self, user_id: str, *, limit: int = 50) -> list[TaskRecord]:
        return await asyncio.to_thread(self._list_by_user_sync, user_id, limit)

    def _list_by_user_sync(self, user_id: str, limit: int) -> list[TaskRecord]:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            rows = conn.execute(
                """
                SELECT * FROM procurement_task
                 WHERE user_id = ?
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task_id=row["task_id"],
        thread_id=row["thread_id"],
        conversation_id=row["conversation_id"],
        user_id=row["user_id"],
        status=row["status"],
        current_node=row["current_node"],
        retry_from=row["retry_from"],
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resumed_at=row["resumed_at"],
        completed_at=row["completed_at"],
    )
