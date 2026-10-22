import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

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
    created_at    TEXT NOT NULL,
    decided_at    TEXT,
    expires_at    TEXT,
    UNIQUE (task_id, plan_version, plan_hash)
);

CREATE INDEX IF NOT EXISTS idx_procurement_approval_task
    ON procurement_approval(task_id, status);
"""


class SqliteApprovalRepository:
    def __init__(self, path: str | Path):
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def ensure_pending(self, record: ApprovalRecord) -> None:
        await asyncio.to_thread(self._ensure_pending_sync, record)

    def _ensure_pending_sync(self, record: ApprovalRecord) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT INTO procurement_approval (
                    approval_id, task_id, plan_version, plan_hash, status,
                    requested_by, approved_by, decision, comment,
                    created_at, decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, ?)
                ON CONFLICT(approval_id) DO NOTHING
                """,
                (
                    record.approval_id,
                    record.task_id,
                    record.plan_version,
                    record.plan_hash,
                    record.status.value,
                    record.requested_by,
                    record.created_at.isoformat(),
                    record.expires_at.isoformat() if record.expires_at else None,
                ),
            )
            conn.commit()

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
        return await asyncio.to_thread(
            self._decide_sync,
            approval_id,
            task_id,
            plan_version,
            plan_hash,
            status,
            decided_by,
            decision,
            comment,
        )

    def _decide_sync(
            self,
            approval_id: str,
            task_id: str,
            plan_version: int,
            plan_hash: str,
            status: str,
            decided_by: str,
            decision: str,
            comment: str | None,
    ) -> ApprovalDecisionResult:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            cursor = conn.execute(
                """
                UPDATE procurement_approval
                   SET status = ?,
                       approved_by = ?,
                       decision = ?,
                       comment = ?,
                       decided_at = ?
                 WHERE approval_id = ?
                   AND task_id = ?
                   AND plan_version = ?
                   AND plan_hash = ?
                   AND status = 'pending'
                """,
                (
                    status,
                    decided_by,
                    decision,
                    comment,
                    datetime.now().astimezone().isoformat(),
                    approval_id,
                    task_id,
                    plan_version,
                    plan_hash,
                ),
            )
            conn.commit()
            if cursor.rowcount == 1:
                return ApprovalDecisionResult.APPLIED

            row = conn.execute(
                "SELECT * FROM procurement_approval WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
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
        return await asyncio.to_thread(self._get_sync, approval_id)

    def _get_sync(self, approval_id: str) -> ApprovalRecord | None:
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT * FROM procurement_approval WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return None
            return ApprovalRecord(
                approval_id=row["approval_id"],
                task_id=row["task_id"],
                plan_version=row["plan_version"],
                plan_hash=row["plan_hash"],
                status=row["status"],
                requested_by=row["requested_by"],
                approved_by=row["approved_by"],
                decision=row["decision"],
                comment=row["comment"],
                created_at=row["created_at"],
                decided_at=row["decided_at"],
                expires_at=row["expires_at"],
            )
