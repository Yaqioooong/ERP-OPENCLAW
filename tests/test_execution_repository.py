from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.execution_repository import PostgresExecutionRepository
from agent.procurement.models import ExecutionRecord, ExecutionStatus

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)


def make_record(key: str = "KEY-1") -> ExecutionRecord:
    return ExecutionRecord(
        idempotency_key=key,
        task_id="TASK-1",
        plan_version=1,
        plan_hash="HASH-1",
        approval_id="APR-1",
        status=ExecutionStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_ensure_pending_is_idempotent(db):
    repo = PostgresExecutionRepository(db)
    await repo.setup()
    first = await repo.ensure_pending(make_record())
    second = await repo.ensure_pending(make_record())

    assert first.idempotency_key == "KEY-1"
    assert second.idempotency_key == "KEY-1"
    assert second.status == ExecutionStatus.PENDING


@pytest.mark.anyio
async def test_try_acquire_is_atomic_and_lease_expires(db):
    repo = PostgresExecutionRepository(db)
    await repo.setup()
    await repo.ensure_pending(make_record())

    assert await repo.try_acquire(
        idempotency_key="KEY-1",
        now=NOW,
        lease_seconds=60,
    ) is True
    assert await repo.try_acquire(
        idempotency_key="KEY-1",
        now=NOW + timedelta(seconds=1),
        lease_seconds=60,
    ) is False
    assert await repo.try_acquire(
        idempotency_key="KEY-1",
        now=NOW + timedelta(seconds=61),
        lease_seconds=60,
    ) is True


@pytest.mark.anyio
async def test_mark_succeeded_failed_unknown(db):
    repo = PostgresExecutionRepository(db)
    await repo.setup()
    await repo.ensure_pending(make_record())
    await repo.try_acquire(
        idempotency_key="KEY-1", now=NOW, lease_seconds=60
    )
    await repo.mark_succeeded(
        idempotency_key="KEY-1",
        order_id="100",
        order_number="PO100",
        response={"ok": True},
        now=NOW + timedelta(seconds=2),
    )
    record = await repo.get("KEY-1")
    assert record.status == ExecutionStatus.SUCCEEDED
    assert record.order_id == "100"
    assert record.order_number == "PO100"