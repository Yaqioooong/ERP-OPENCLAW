"""PostgreSQL 审批记录仓储。"""
from __future__ import annotations

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

    async def ensure_pending(self, record: ApprovalRecord) -> None:
        await self.setup()
        async with self._database.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO procurement_approval (
                    approval_id, task_id, plan_version, plan_hash, status,
                    requested_by, approved_by, decision, comment,
                    created_at, decided_at, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6, NULL, NULL, NULL, $7, NULL, $8)
                ON CONFLICT (approval_id) DO NOTHING
                """,
                record.approval_id,
                record.task_id,
                record.plan_version,
                record.plan_hash,
                record.status.value,
                record.requested_by,
                record.created_at,
                record.expires_at,
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
                       decided_at = $5
                 WHERE approval_id = $6
                   AND task_id = $7
                   AND plan_version = $8
                   AND plan_hash = $9
                   AND status = 'pending'
                """,
                status,
                decided_by,
                decision,
                comment,
                datetime.now().astimezone(),
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
        return ApprovalRecord(**dict(row))
