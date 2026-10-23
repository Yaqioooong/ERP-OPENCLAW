"""PostgreSQL 订单执行记录仓储。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Protocol

from agent.procurement.database import Database
from agent.procurement.models import ExecutionRecord, ExecutionStatus

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
    plan_snapshot     JSONB,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    verification_attempts INTEGER NOT NULL DEFAULT 0,
    requires_manual_review INTEGER NOT NULL DEFAULT 0,
    remote_status     INTEGER,
    last_error_code   TEXT,
    last_error_message TEXT,
    request_payload   JSONB,
    response_payload  JSONB,
    verification_payload JSONB,
    lease_expires_at  TIMESTAMPTZ,
    reconcile_lease_expires_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    executed_at       TIMESTAMPTZ,
    last_verified_at  TIMESTAMPTZ,
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

    async def list_needing_reconciliation(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[ExecutionRecord]: ...

    async def try_acquire_reconcile(
        self,
        *,
        idempotency_key: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool: ...

    async def mark_verified_succeeded(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        order_number: str,
        remote_status: int | None,
        response: dict | None,
        verification: dict | None,
        now: datetime,
    ) -> None: ...

    async def mark_not_created(
        self,
        *,
        idempotency_key: str,
        message: str,
        now: datetime,
    ) -> None: ...

    async def mark_cancelled(
        self,
        *,
        idempotency_key: str,
        remote_status: int | None,
        message: str,
        now: datetime,
    ) -> None: ...

    async def mark_mismatch(
        self,
        *,
        idempotency_key: str,
        message: str,
        verification: dict | None,
        now: datetime,
    ) -> None: ...

    async def mark_still_unknown(
        self,
        *,
        idempotency_key: str,
        message: str,
        verification: dict | None,
        now: datetime,
    ) -> None: ...


class PostgresExecutionRepository:
    def __init__(self, database: Database):
        self._database = database

    async def setup(self) -> None:
        async with self._database.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def ensure_pending(self, record: ExecutionRecord) -> ExecutionRecord:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO procurement_order_execution (
                    idempotency_key, task_id, plan_version, plan_hash,
                    approval_id, status, order_id, order_number,
                    plan_snapshot, attempt_count, last_error_code, last_error_message,
                    request_payload, response_payload, lease_expires_at,
                    created_at, updated_at, executed_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, NULL, $7,
                    $8::jsonb, 0, NULL, NULL, $9::jsonb, NULL, NULL,
                    $10, $11, NULL
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                record.idempotency_key,
                record.task_id,
                record.plan_version,
                record.plan_hash,
                record.approval_id,
                ExecutionStatus.PENDING.value,
                record.order_number,
                _json(record.plan_snapshot),
                _json(record.request_payload),
                record.created_at,
                record.updated_at,
            )
        loaded = await self.get(record.idempotency_key)
        if loaded is None:
            raise RuntimeError("执行记录写入后无法读取")
        return loaded

    async def get(self, idempotency_key: str) -> ExecutionRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM procurement_order_execution WHERE idempotency_key = $1",
                idempotency_key,
            )
        return _row_to_record(row) if row else None

    async def get_by_task(self, task_id: str) -> ExecutionRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM procurement_order_execution
                 WHERE task_id = $1
                 ORDER BY updated_at DESC
                 LIMIT 1
                """,
                task_id,
            )
        return _row_to_record(row) if row else None

    async def get_by_order_number(self, order_number: str) -> ExecutionRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM procurement_order_execution WHERE order_number = $1",
                order_number,
            )
        return _row_to_record(row) if row else None

    async def try_acquire(
        self,
        *,
        idempotency_key: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        await self.setup()
        lease = now + timedelta(seconds=lease_seconds)
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE procurement_order_execution
                   SET status = 'executing',
                       attempt_count = attempt_count + 1,
                       lease_expires_at = $1,
                       updated_at = $2
                 WHERE idempotency_key = $3
                   AND (
                       status = 'pending'
                       OR (
                           status = 'executing'
                           AND (lease_expires_at IS NULL OR lease_expires_at < $2)
                       )
                   )
                RETURNING idempotency_key
                """,
                lease,
                now,
                idempotency_key,
            )
        return row is not None

    async def mark_succeeded(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        order_number: str,
        response: dict | None,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'succeeded',
                       order_id = $1,
                       order_number = $2,
                       response_payload = $3::jsonb,
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = NULL,
                       last_error_message = NULL,
                       updated_at = $4,
                       executed_at = $4
                 WHERE idempotency_key = $5
                """,
                order_id,
                order_number,
                _json(response),
                now,
                idempotency_key,
            )

    async def mark_failed(
        self,
        *,
        idempotency_key: str,
        code: str,
        message: str,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'failed',
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = $1,
                       last_error_message = $2,
                       updated_at = $3
                 WHERE idempotency_key = $4
                """,
                code,
                message,
                now,
                idempotency_key,
            )

    async def mark_unknown(
        self,
        *,
        idempotency_key: str,
        code: str,
        message: str,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'unknown',
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = $1,
                       last_error_message = $2,
                       updated_at = $3
                 WHERE idempotency_key = $4
                """,
                code,
                message,
                now,
                idempotency_key,
            )

    async def list_needing_reconciliation(
        self,
        *,
        now: datetime,
        limit: int = 50,
    ) -> list[ExecutionRecord]:
        await self.setup()
        async with self._database.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM procurement_order_execution
                 WHERE status IN ('unknown', 'executing')
                   AND (
                       reconcile_lease_expires_at IS NULL
                       OR reconcile_lease_expires_at < $1
                   )
                   AND (
                       status != 'executing'
                       OR lease_expires_at IS NULL
                       OR lease_expires_at < $1
                   )
                 ORDER BY updated_at ASC
                 LIMIT $2
                """,
                now,
                limit,
            )
        return [_row_to_record(row) for row in rows]

    async def try_acquire_reconcile(
        self,
        *,
        idempotency_key: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        await self.setup()
        lease = now + timedelta(seconds=lease_seconds)
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE procurement_order_execution
                   SET reconcile_lease_expires_at = $1,
                       updated_at = $2
                 WHERE idempotency_key = $3
                   AND (
                       reconcile_lease_expires_at IS NULL
                       OR reconcile_lease_expires_at < $2
                   )
                RETURNING idempotency_key
                """,
                lease,
                now,
                idempotency_key,
            )
        return row is not None

    async def mark_verified_succeeded(
        self,
        *,
        idempotency_key: str,
        order_id: str,
        order_number: str,
        remote_status: int | None,
        response: dict | None,
        verification: dict | None,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'succeeded',
                       order_id = $1,
                       order_number = $2,
                       remote_status = $3,
                       response_payload = $4::jsonb,
                       verification_payload = $5::jsonb,
                       verification_attempts = verification_attempts + 1,
                       last_verified_at = $6,
                       requires_manual_review = 0,
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = NULL,
                       last_error_message = NULL,
                       updated_at = $6,
                       executed_at = COALESCE(executed_at, $6)
                 WHERE idempotency_key = $7
                """,
                order_id,
                order_number,
                remote_status,
                _json(response),
                _json(verification),
                now,
                idempotency_key,
            )

    async def mark_not_created(
        self,
        *,
        idempotency_key: str,
        message: str,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'failed',
                       requires_manual_review = 0,
                       verification_attempts = verification_attempts + 1,
                       last_verified_at = $1,
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = 'ORDER_NOT_FOUND_CONFIRMED',
                       last_error_message = $2,
                       updated_at = $1
                 WHERE idempotency_key = $3
                """,
                now,
                message,
                idempotency_key,
            )

    async def mark_cancelled(
        self,
        *,
        idempotency_key: str,
        remote_status: int | None,
        message: str,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'failed',
                       remote_status = $1,
                       requires_manual_review = 0,
                       verification_attempts = verification_attempts + 1,
                       last_verified_at = $2,
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = 'ORDER_CANCELLED_REMOTE',
                       last_error_message = $3,
                       updated_at = $2
                 WHERE idempotency_key = $4
                """,
                remote_status,
                now,
                message,
                idempotency_key,
            )

    async def mark_mismatch(
        self,
        *,
        idempotency_key: str,
        message: str,
        verification: dict | None,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'needs_review',
                       requires_manual_review = 1,
                       verification_attempts = verification_attempts + 1,
                       last_verified_at = $1,
                       lease_expires_at = NULL,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = 'ORDER_CONTENT_MISMATCH',
                       last_error_message = $2,
                       verification_payload = $3::jsonb,
                       updated_at = $1
                 WHERE idempotency_key = $4
                """,
                now,
                message,
                _json(verification),
                idempotency_key,
            )

    async def mark_still_unknown(
        self,
        *,
        idempotency_key: str,
        message: str,
        verification: dict | None,
        now: datetime,
    ) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                UPDATE procurement_order_execution
                   SET status = 'unknown',
                       verification_attempts = verification_attempts + 1,
                       last_verified_at = $1,
                       reconcile_lease_expires_at = NULL,
                       last_error_code = 'ORDER_RESULT_UNKNOWN',
                       last_error_message = $2,
                       verification_payload = $3::jsonb,
                       updated_at = $1
                 WHERE idempotency_key = $4
                """,
                now,
                message,
                _json(verification),
                idempotency_key,
            )


def _json(value: dict | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def _row_to_record(row: Any) -> ExecutionRecord:
    data = dict(row)
    data["plan_snapshot"] = _load_json(data.get("plan_snapshot"))
    data["request_payload"] = _load_json(data.get("request_payload"))
    data["response_payload"] = _load_json(data.get("response_payload"))
    data["verification_payload"] = _load_json(data.get("verification_payload"))
    data["requires_manual_review"] = bool(data.get("requires_manual_review"))
    return ExecutionRecord(**data)
