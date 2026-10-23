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

@pytest.mark.anyio
async def test_rejected_approval_can_be_reprepared(db):
    repo = PostgresApprovalRepository(db)
    record = make_record()
    await repo.ensure_pending(record)
    await repo.decide(
        approval_id=record.approval_id,
        task_id=record.task_id,
        plan_version=record.plan_version,
        plan_hash=record.plan_hash,
        status="rejected",
        decided_by="u1",
        decision="reject",
        comment="先不买",
    )
    assert (await repo.get(record.approval_id)).status == ApprovalStatus.REJECTED

    await repo.ensure_pending(record)
    reloaded = await repo.get(record.approval_id)
    assert reloaded.status == ApprovalStatus.PENDING
    assert reloaded.decision is None
    assert reloaded.approved_by is None


@pytest.mark.anyio
async def test_analysis_report_is_persisted(db):
    repo = PostgresApprovalRepository(db)
    record = make_record()
    record.analysis_report = "# analysis"
    await repo.ensure_pending(record)

    loaded = await repo.get(record.approval_id)
    assert loaded is not None
    assert loaded.analysis_report == "# analysis"
