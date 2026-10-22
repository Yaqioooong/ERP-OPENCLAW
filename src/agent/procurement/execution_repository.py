import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from agent.procurement.models import ExecutionRecord, ExecutionStatus
from agent.procurement.task_repository import _row_to_record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS procurement_order_execution (
    idempotency_key   TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    plan_version      INTEGER NOT NULL,
    plan_hash         TEXT NOT NULL,
    approval_id       TEXT NOT NULL,
    status            TEXT NOT NULL,
    order_id          TEXT,
    order_number      TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    last_error_code   TEXT,
    last_error_message TEXT,
    request_payload   TEXT,
    response_payload  TEXT,
    lease_expires_at  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    executed_at       TEXT,
    UNIQUE (task_id, plan_version, plan_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_procurement_execution_order_number
    ON procurement_order_execution(order_number)
    WHERE order_number IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_procurement_execution_task
    ON procurement_order_execution(task_id, updated_at);
"""


class ExecutionRepository(Protocol):
    async def setup(self) -> None: ...

    async def ensure_pending(self, record: ExecutionRecord) -> ExecutionRecord: ...

    async def get(self, idempotency_key: str) -> ExecutionRecord | None: ...

    async def get_by_task(self, task_id: str) -> ExecutionRecord | None: ...

    async def get_by_order_number(self, order_number: str) -> ExecutionRecord | None: ...

    async def try_acquire(
            self,
            *,
            idempotency_key: str,
            now: datetime,
            lease_seconds: int,
    ) -> bool: ...

    async def mark_succeeded(
            self,
            *,
            idempotency_key: str,
            order_id: str,
            order_number: str,
            response: dict | None,
            now: datetime,
    ) -> None: ...

    async def mark_failed(
            self,
            *,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None: ...

    async def mark_unknown(
            self,
            *,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None: ...


class SqliteExecutionRepository:
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

    async def ensure_pending(self, record: ExecutionRecord) -> ExecutionRecord:
        await asyncio.to_thread(self._ensure_pending_sync, record)
        loaded = await self.get(record.idempotency_key)
        if loaded is None:
            raise RuntimeError("执行记录写入后无法读取")
        return loaded

    def _ensure_pending_sync(self, record: ExecutionRecord) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT INTO procurement_order_execution (
                    idempotency_key, task_id, plan_version, plan_hash,
                    approval_id, status, order_id, order_number,
                    attempt_count, last_error_code, last_error_message,
                    request_payload, response_payload, lease_expires_at,
                    created_at, updated_at, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, NULL, NULL, ?, NULL, NULL, ?, ?, NULL)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    record.idempotency_key,
                    record.task_id,
                    record.plan_version,
                    record.plan_hash,
                    record.approval_id,
                    ExecutionStatus.PENDING.value,
                    record.order_number,
                    json.dumps(record.request_payload, ensure_ascii=False, default=str)
                    if record.request_payload is not None
                    else None,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            conn.commit()

    async def get(self, idempotency_key: str) -> ExecutionRecord | None:
        return await asyncio.to_thread(self._get_sync, idempotency_key)

    def _get_sync(self, idempotency_key: str) -> ExecutionRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM procurement_order_execution WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_record(row) if row else None

    async def get_by_task(self, task_id: str) -> ExecutionRecord | None:
        return await asyncio.to_thread(self._get_by_task_sync, task_id)

    def _get_by_task_sync(self, task_id: str) -> ExecutionRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                """
                SELECT * FROM procurement_order_execution
                 WHERE task_id = ?
                 ORDER BY updated_at DESC
                 LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    async def get_by_order_number(self, order_number: str) -> ExecutionRecord | None:
        return await asyncio.to_thread(self._get_by_order_number_sync, order_number)

    def _get_by_order_number_sync(self, order_number: str) -> ExecutionRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM procurement_order_execution WHERE order_number = ?",
                (order_number,),
            ).fetchone()
        return _row_to_record(row) if row else None

    async def try_acquire(
            self,
            *,
            idempotency_key: str,
            now: datetime,
            lease_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._try_acquire_sync,
            idempotency_key,
            now,
            lease_seconds,
        )

    def _try_acquire_sync(
            self,
            idempotency_key: str,
            now: datetime,
            lease_seconds: int,
    ) -> bool:
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            cursor = conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'executing',
                       attempt_count = attempt_count + 1,
                       lease_expires_at = ?,
                       updated_at = ?
                 WHERE idempotency_key = ?
                   AND (
                       status = 'pending'
                       OR (
                           status = 'executing'
                           AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                       )
                   )
                """,
                (lease, now.isoformat(), idempotency_key, now.isoformat()),
            )
            conn.commit()
            return cursor.rowcount == 1

    async def mark_succeeded(
            self,
            *,
            idempotency_key: str,
            order_id: str,
            order_number: str,
            response: dict | None,
            now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._mark_succeeded_sync,
            idempotency_key,
            order_id,
            order_number,
            response,
            now,
        )

    def _mark_succeeded_sync(
            self,
            idempotency_key: str,
            order_id: str,
            order_number: str,
            response: dict | None,
            now: datetime,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'succeeded',
                       order_id = ?,
                       order_number = ?,
                       response_payload = ?,
                       lease_expires_at = NULL,
                       last_error_code = NULL,
                       last_error_message = NULL,
                       updated_at = ?,
                       executed_at = ?
                 WHERE idempotency_key = ?
                """,
                (
                    order_id,
                    order_number,
                    json.dumps(response, ensure_ascii=False, default=str)
                    if response is not None
                    else None,
                    now.isoformat(),
                    now.isoformat(),
                    idempotency_key,
                ),
            )
            conn.commit()

    async def mark_failed(
            self,
            *,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._mark_failed_sync, idempotency_key, code, message, now
        )

    def _mark_failed_sync(
            self,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'failed',
                       lease_expires_at = NULL,
                       last_error_code = ?,
                       last_error_message = ?,
                       updated_at = ?
                 WHERE idempotency_key = ?
                """,
                (code, message, now.isoformat(), idempotency_key),
            )
            conn.commit()

    async def mark_unknown(
            self,
            *,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None:
        await asyncio.to_thread(
            self._mark_unknown_sync, idempotency_key, code, message, now
        )

    def _mark_unknown_sync(
            self,
            idempotency_key: str,
            code: str,
            message: str,
            now: datetime,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'unknown',
                       lease_expires_at = NULL,
                       last_error_code = ?,
                       last_error_message = ?,
                       updated_at = ?
                 WHERE idempotency_key = ?
                """,
                (code, message, now.isoformat(), idempotency_key),
            )
            conn.commit()


def _row_to_record(row: sqlite3.Row) -> ExecutionRecord:
    def load_json(value: str | None) -> dict | None:
        if not value:
            return None
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    return ExecutionRecord(
        idempotency_key=row["idempotency_key"],
        task_id=row["task_id"],
        plan_version=row["plan_version"],
        plan_hash=row["plan_hash"],
        approval_id=row["approval_id"],
        status=row["status"],
        order_id=row["order_id"],
        order_number=row["order_number"],
        attempt_count=row["attempt_count"],
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        request_payload=load_json(row["request_payload"]),
        response_payload=load_json(row["response_payload"]),
        lease_expires_at=row["lease_expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        executed_at=row["executed_at"],
    )
