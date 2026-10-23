"""One-off migration: SQLite procurement facts -> PostgreSQL.

Usage:
    .venv/bin/python scripts/migrate_sqlite_to_postgres.py \
        --sqlite-path data/procurement.db \
        --database-url postgresql:///erp_openclaw
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent.procurement.approval_repository import PostgresApprovalRepository
from agent.procurement.database import Database
from agent.procurement.execution_repository import PostgresExecutionRepository
from agent.procurement.task_repository import PostgresTaskRepository


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(json.loads(value), ensure_ascii=False)
    except json.JSONDecodeError:
        return value


def _rows(sqlite_path: Path, table: str) -> list[dict]:
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in con.execute(f'SELECT * FROM "{table}"')]
    finally:
        con.close()


async def migrate(sqlite_path: Path, database_url: str) -> None:
    database = Database(database_url)
    await database.connect()
    try:
        tasks = PostgresTaskRepository(database)
        approvals = PostgresApprovalRepository(database)
        executions = PostgresExecutionRepository(database)
        await tasks.setup()
        await approvals.setup()
        await executions.setup()

        migrated = {"task": 0, "approval": 0, "execution": 0}

        async with database.acquire() as conn:
            for row in _rows(sqlite_path, "procurement_task"):
                result = await conn.execute(
                    """
                    INSERT INTO procurement_task (
                        task_id, thread_id, conversation_id, user_id, status,
                        current_node, retry_from, last_error_code, last_error_message,
                        created_at, updated_at, resumed_at, completed_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (task_id) DO NOTHING
                    """,
                    row["task_id"], row["thread_id"], row.get("conversation_id"),
                    row["user_id"], row["status"], row.get("current_node"),
                    row.get("retry_from"), row.get("last_error_code"),
                    row.get("last_error_message"), _dt(row["created_at"]),
                    _dt(row["updated_at"]), _dt(row.get("resumed_at")),
                    _dt(row.get("completed_at")),
                )
                migrated["task"] += 1 if result == "INSERT 0 1" else 0

            for row in _rows(sqlite_path, "procurement_approval"):
                result = await conn.execute(
                    """
                    INSERT INTO procurement_approval (
                        approval_id, task_id, plan_version, plan_hash, status,
                        requested_by, approved_by, decision, comment,
                        created_at, decided_at, expires_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (approval_id) DO NOTHING
                    """,
                    row["approval_id"], row["task_id"], row["plan_version"],
                    row["plan_hash"], row["status"], row["requested_by"],
                    row.get("approved_by"), row.get("decision"), row.get("comment"),
                    _dt(row["created_at"]), _dt(row.get("decided_at")),
                    _dt(row.get("expires_at")),
                )
                migrated["approval"] += 1 if result == "INSERT 0 1" else 0

            for row in _rows(sqlite_path, "procurement_order_execution"):
                result = await conn.execute(
                    """
                    INSERT INTO procurement_order_execution (
                        idempotency_key, task_id, plan_version, plan_hash,
                        approval_id, status, order_id, order_number, plan_snapshot,
                        attempt_count, verification_attempts, requires_manual_review,
                        remote_status, last_error_code, last_error_message,
                        request_payload, response_payload, verification_payload,
                        lease_expires_at, reconcile_lease_expires_at,
                        created_at, updated_at, executed_at, last_verified_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,
                        $10,$11,$12,$13,$14,$15,
                        $16::jsonb,$17::jsonb,$18::jsonb,
                        $19,$20,$21,$22,$23,$24
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    row["idempotency_key"], row["task_id"], row["plan_version"],
                    row["plan_hash"], row["approval_id"], row["status"],
                    row.get("order_id"), row.get("order_number"),
                    _json(row.get("plan_snapshot")), row.get("attempt_count", 0),
                    row.get("verification_attempts", 0),
                    bool(row.get("requires_manual_review", 0)),
                    row.get("remote_status"), row.get("last_error_code"),
                    row.get("last_error_message"), _json(row.get("request_payload")),
                    _json(row.get("response_payload")),
                    _json(row.get("verification_payload")),
                    _dt(row.get("lease_expires_at")),
                    _dt(row.get("reconcile_lease_expires_at")),
                    _dt(row["created_at"]), _dt(row["updated_at"]),
                    _dt(row.get("executed_at")), _dt(row.get("last_verified_at")),
                )
                migrated["execution"] += 1 if result == "INSERT 0 1" else 0

        print(
            "migrated: "
            f"tasks={migrated['task']} approvals={migrated['approval']} "
            f"executions={migrated['execution']}"
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", default="data/procurement.db")
    parser.add_argument(
        "--database-url", default="postgresql:///erp_openclaw"
    )
    args = parser.parse_args()
    asyncio.run(migrate(Path(args.sqlite_path), args.database_url))


if __name__ == "__main__":
    main()
