import pytest

from agent.procurement.approval_repository import PostgresApprovalRepository
from agent.procurement.models import ApprovalDecisionResult, ApprovalStatus
from tests.factories import make_record


@pytest.mark.anyio
async def test_ensure_pending_is_idempotent(db):
    repo = PostgresApprovalRepository(db)
    record = make_record()
    await repo.ensure_pending(record)
    await repo.ensure_pending(record)
    assert (await repo.get(record.approval_id)).status == ApprovalStatus.PENDING


@pytest.mark.anyio
async def test_decide_rejects_wrong_plan_hash(db):
    repo = PostgresApprovalRepository(db)
    record = make_record()
    await repo.ensure_pending(record)
    ok = await repo.decide(
        approval_id=record.approval_id,
        task_id=record.task_id,
        plan_version=record.plan_version,
        plan_hash="wrong",
        status="approved",
        decided_by="u1",
        decision="approve",
        comment=None,
    )
    assert ok == ApprovalDecisionResult.STALE


@pytest.mark.anyio
async def test_decide_cannot_reuse_approved_record(db):
    repo = PostgresApprovalRepository(db)
    record = make_record()
    await repo.ensure_pending(record)

    approve_kwargs = dict(
        approval_id=record.approval_id,
        task_id=record.task_id,
        plan_version=record.plan_version,
        plan_hash=record.plan_hash,
        status="approved",
        decided_by="u1",
        decision="approve",
        comment=None,
    )
    reject_kwargs = {**approve_kwargs, "status": "rejected", "decision": "reject"}

    assert await repo.decide(**approve_kwargs) == ApprovalDecisionResult.APPLIED
    assert await repo.decide(**reject_kwargs) == ApprovalDecisionResult.STALE
    assert await repo.decide(**approve_kwargs) == ApprovalDecisionResult.ALREADY_APPLIED