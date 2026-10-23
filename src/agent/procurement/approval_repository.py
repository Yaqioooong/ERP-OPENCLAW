"""PostgreSQL 审批记录仓储。"""
from __future__ import annotations

import json
from datetime import datetime

from agent.procurement.database import Database
from agent.procurement.models import ApprovalDecisionResult, ApprovalRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS procurement_approval (
    approval_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    plan_version  INTEGER NOT NULL,
    plan_hash     TEXT NOT NULL,
    status        TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    approved_by   TEXT,
    decision      TEXT,
    comment       TEXT,
    created_at    TIMESTAMPTZ NOT NULL,
    decided_at    TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    plan_snapshot JSONB,
    analysis_report TEXT,
    approved_by_roles TEXT[],
    UNIQUE (task_id, plan_version, plan_hash)
);

CREATE INDEX IF NOT EXISTS idx_procurement_approval_task
    ON procurement_approval(task_id, status);
"""


class PostgresApprovalRepository:
    def __init__(self, database: Database):
        self._database = database

    async def setup(self) -> None:
        async with self._database.acquire() as conn:
            await conn.execute(_SCHEMA)
            await conn.execute(
                "ALTER TABLE procurement_approval "
                "ADD COLUMN IF NOT EXISTS plan_snapshot JSONB"
            )
            await conn.execute(
                "ALTER TABLE procurement_approval "
                "ADD COLUMN IF NOT EXISTS approved_by_roles TEXT[]"
            )
            await conn.execute(
                "ALTER TABLE procurement_approval "
                "ADD COLUMN IF NOT EXISTS analysis_report TEXT"
            )

    async def ensure_pending(self, record: ApprovalRecord) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO procurement_approval (
                    approval_id, task_id, plan_version, plan_hash, status,
                    requested_by, approved_by, decision, comment,
                    created_at, decided_at, expires_at, plan_snapshot,
                    analysis_report, approved_by_roles
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, NULL, NULL, NULL, $7, NULL, $8,
                    $9::jsonb, $10, NULL
                )
                ON CONFLICT (approval_id) DO UPDATE
                   SET status = 'pending',
                       requested_by = EXCLUDED.requested_by,
                       created_at = EXCLUDED.created_at,
                       expires_at = EXCLUDED.expires_at,
                       plan_snapshot = EXCLUDED.plan_snapshot,
                       analysis_report = EXCLUDED.analysis_report,
                       approved_by = NULL,
                       approved_by_roles = NULL,
                       decision = NULL,
                       comment = NULL,
                       decided_at = NULL
                 WHERE procurement_approval.status IN ('rejected', 'stale')
                    OR (
                        procurement_approval.status = 'pending'
                        AND procurement_approval.expires_at IS NOT NULL
                        AND procurement_approval.expires_at < EXCLUDED.created_at
                    )
                """,
                record.approval_id,
                record.task_id,
                record.plan_version,
                record.plan_hash,
                record.status.value,
                record.requested_by,
                record.created_at,
                record.expires_at,
                _json(record.plan_snapshot),
                record.analysis_report,
            )

    async def decide(
        self,
        *,
        approval_id: str,
        task_id: str,
        plan_version: int,
        plan_hash: str,
        status: str,
        decided_by: str,
        decision: str,
        comment: str | None,
        decided_by_roles: list[str] | None = None,
    ) -> ApprovalDecisionResult:
        await self.setup()
        async with self._database.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE procurement_approval
                   SET status = $1,
                       approved_by = $2,
                       decision = $3,
                       comment = $4,
                       decided_at = $5,
                       approved_by_roles = $6
                 WHERE approval_id = $7
                   AND task_id = $8
                   AND plan_version = $9
                   AND plan_hash = $10
                   AND status = 'pending'
                """,
                status,
                decided_by,
                decision,
                comment,
                datetime.now().astimezone(),
                decided_by_roles or [],
                approval_id,
                task_id,
                plan_version,
                plan_hash,
            )
            if result == "UPDATE 1":
                return ApprovalDecisionResult.APPLIED

            row = await conn.fetchrow(
                "SELECT * FROM procurement_approval WHERE approval_id = $1",
                approval_id,
            )
            if (
                row is not None
                and row["status"] == status
                and row["decision"] == decision
                and row["plan_version"] == plan_version
                and row["plan_hash"] == plan_hash
            ):
                return ApprovalDecisionResult.ALREADY_APPLIED
            return ApprovalDecisionResult.STALE

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        await self.setup()
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM procurement_approval WHERE approval_id = $1",
                approval_id,
            )
        if row is None:
            return None
        data = dict(row)
        data["approved_by_roles"] = data.get("approved_by_roles") or []
        data["plan_snapshot"] = _load_json(data.get("plan_snapshot"))
        return ApprovalRecord(**data)


def _json(value: dict | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value):
    if value is None or isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None
