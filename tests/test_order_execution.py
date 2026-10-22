import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.approval_repository import SqliteApprovalRepository
from agent.procurement.erp_order_gateway import (
    GatewayOrder,
    GatewayTimeout,
)
from agent.procurement.execution_repository import SqliteExecutionRepository
from agent.procurement.models import (
    ApprovalRecord,
    ApprovalStatus,
    ExecutionStatus,
    ProcurementPlan,
)
from agent.procurement.order_execution import SafeOrderExecutionService
from agent.procurement.rules import compute_plan_hash, make_idempotency_key
from tests.factories import make_plan

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)


class FakeGateway:
    def __init__(self, *, timeout: bool = False, existing=None):
        self.timeout = timeout
        self.existing = existing
        self.created = []
        self.by_number = {}
        self.by_id = {}

    async def create_order(self, *, order_number, plan, idempotency_key):
        self.created.append(order_number)
        if self.timeout:
            raise GatewayTimeout("模拟超时")
        order = GatewayOrder(
            order_id="100",
            order_number=order_number,
            raw={"orderId": 100, "orderNumber": order_number},
        )
        self.by_number[order_number] = order
        self.by_id["100"] = order
        return order

    async def get_order_by_number(self, order_number):
        return self.by_number.get(order_number) or self.existing

    async def get_order_by_id(self, order_id):
        return self.by_id.get(str(order_id))


async def make_service(tmp_path, gateway):
    approvals = SqliteApprovalRepository(tmp_path / "approval.db")
    executions = SqliteExecutionRepository(tmp_path / "exec.db")
    await executions.setup()

    plan = make_plan()
    approval = ApprovalRecord(
        approval_id="APR-1",
        task_id="TASK-1",
        plan_version=plan.version,
        plan_hash=compute_plan_hash(plan),
        status=ApprovalStatus.PENDING,
        requested_by="u1",
        created_at=NOW,
        expires_at=NOW,
    )
    await approvals.ensure_pending(approval)
    await approvals.decide(
        approval_id="APR-1",
        task_id="TASK-1",
        plan_version=plan.version,
        plan_hash=approval.plan_hash,
        status=ApprovalStatus.APPROVED.value,
        decided_by="u1",
        decision="approve",
        comment=None,
    )

    service = SafeOrderExecutionService(
        execution_repository=executions,
        approval_repository=approvals,
        gateway=gateway,
        now=lambda: NOW,
    )
    return service, plan, approvals, executions


@pytest.mark.anyio
async def test_same_key_creates_once(tmp_path):
    gateway = FakeGateway()
    service, plan, _, _ = await make_service(tmp_path, gateway)
    key = make_idempotency_key("TASK-1", plan.version, compute_plan_hash(plan))

    first = await service.create_order(
        task_id="TASK-1",
        plan=plan,
        approval_id="APR-1",
        idempotency_key=key,
    )
    second = await service.create_order(
        task_id="TASK-1",
        plan=plan,
        approval_id="APR-1",
        idempotency_key=key,
    )

    assert first.status == ExecutionStatus.SUCCEEDED
    assert second.status == ExecutionStatus.SUCCEEDED
    assert len(gateway.created) == 1


class SlowGateway(FakeGateway):
    async def create_order(self, *, order_number, plan, idempotency_key):
        await asyncio.sleep(0.05)
        return await super().create_order(
            order_number=order_number,
            plan=plan,
            idempotency_key=idempotency_key,
        )


@pytest.mark.anyio
async def test_concurrent_same_key_creates_once(tmp_path):
    gateway = SlowGateway()
    service, plan, _, _ = await make_service(tmp_path, gateway)
    key = make_idempotency_key("TASK-1", plan.version, compute_plan_hash(plan))

    first, second = await asyncio.gather(
        service.create_order(
            task_id="TASK-1",
            plan=plan,
            approval_id="APR-1",
            idempotency_key=key,
        ),
        service.create_order(
            task_id="TASK-1",
            plan=plan,
            approval_id="APR-1",
            idempotency_key=key,
        ),
    )

    assert len(gateway.created) == 1
    assert {first.status, second.status} <= {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.UNKNOWN,
    }
    assert ExecutionStatus.SUCCEEDED in {first.status, second.status}


@pytest.mark.anyio
async def test_timeout_then_verify_finds_order(tmp_path):
    gateway = FakeGateway(timeout=True)
    service, plan, _, _ = await make_service(tmp_path, gateway)
    key = make_idempotency_key("TASK-1", plan.version, compute_plan_hash(plan))

    created = await service.create_order(
        task_id="TASK-1",
        plan=plan,
        approval_id="APR-1",
        idempotency_key=key,
    )
    assert created.status == ExecutionStatus.UNKNOWN

    # 模拟远端其实已创建
    gateway.existing = GatewayOrder(
        order_id="100",
        order_number=make_order_number_for(plan),
        raw={"orderId": 100},
    )
    verified = await service.verify_order(task_id="TASK-1", order_id=None)

    assert verified.status == ExecutionStatus.SUCCEEDED
    assert len(gateway.created) == 1


def make_order_number_for(plan: ProcurementPlan) -> str:
    from agent.procurement.rules import make_order_number

    return make_order_number(
        task_id=plan.task_id,
        plan_version=plan.version,
        plan_hash=compute_plan_hash(plan),
    )