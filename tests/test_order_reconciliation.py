from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.erp_order_gateway import GatewayOrder, GatewayUnavailable
from agent.procurement.execution_repository import PostgresExecutionRepository
from agent.procurement.models import (
    ExecutionRecord,
    ExecutionStatus,
)
from agent.procurement.order_reconciliation import OrderReconciler
from agent.procurement.rules import compute_plan_hash
from tests.factories import make_plan

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
PLAN = make_plan()
ORDER_NUMBER = "PO20261022TEST"


def make_record(
    *,
    status: ExecutionStatus = ExecutionStatus.UNKNOWN,
    attempts: int = 0,
) -> ExecutionRecord:
    return ExecutionRecord(
        idempotency_key="KEY-1",
        task_id="TASK-1",
        plan_version=1,
        plan_hash=compute_plan_hash(PLAN),
        approval_id="APR-1",
        status=status,
        plan_snapshot=PLAN.model_dump(mode="json"),
        order_number=ORDER_NUMBER,
        verification_attempts=attempts,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeGateway:
    def __init__(self, order: GatewayOrder | None, *, unavailable: bool = False):
        self.order = order
        self.unavailable = unavailable
        self.by_number_calls = 0
        self.by_id_calls = 0

    async def get_order_by_number(self, order_number):
        self.by_number_calls += 1
        if self.unavailable:
            raise GatewayUnavailable("模拟订单服务不可用")
        return self.order

    async def get_order_by_id(self, order_id):
        self.by_id_calls += 1
        if self.unavailable:
            raise GatewayUnavailable("模拟订单服务不可用")
        return self.order


def matched_gateway_order() -> GatewayOrder:
    raw = {
        "orderId": 100,
        "orderNumber": ORDER_NUMBER,
        "status": 2,
        "totalAmount": float(PLAN.total_amount),
        "supplier": {"id": PLAN.supplier_id},
        "orderDetail": [
            {
                "partId": item.part_id,
                "quantity": item.quantity,
                "unitPrice": float(item.unit_price),
                "subtotal": float(item.subtotal),
            }
            for item in PLAN.items
        ],
    }
    return GatewayOrder(
        order_id="100",
        order_number=ORDER_NUMBER,
        raw=raw,
    )


async def make_reconciler(db, gateway, *, record=None):
    repository = PostgresExecutionRepository(db)
    await repository.setup()
    await repository.ensure_pending(record or make_record())
    reconciler = OrderReconciler(
        executions=repository,
        gateway=gateway,
        now=lambda: NOW,
        confirm_attempts=2,
        confirm_min_seconds=0,
        max_attempts=5,
        lease_seconds=60,
    )
    return reconciler, repository


@pytest.mark.anyio
async def test_verified_order_matches_plan(db):
    gateway = FakeGateway(matched_gateway_order())
    reconciler, repository = await make_reconciler(db, gateway)

    result = await reconciler.verify_by_task("TASK-1")

    assert result.status == ExecutionStatus.SUCCEEDED
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.SUCCEEDED
    assert record.order_id == "100"
    assert record.remote_status == 2
    assert record.last_verified_at is not None


@pytest.mark.anyio
async def test_mismatched_quantity_goes_to_needs_review(db):
    order = matched_gateway_order()
    order.raw["orderDetail"][0]["quantity"] += 1
    gateway = FakeGateway(order)
    reconciler, repository = await make_reconciler(db, gateway)

    result = await reconciler.verify_by_task("TASK-1")

    assert result.status == ExecutionStatus.FAILED
    assert result.requires_manual_review is True
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.NEEDS_REVIEW
    assert record.requires_manual_review is True


@pytest.mark.anyio
async def test_not_found_requires_confirmation_window(db):
    gateway = FakeGateway(None)
    reconciler, repository = await make_reconciler(db, gateway)

    first = await reconciler.verify_by_task("TASK-1")
    assert first.status == ExecutionStatus.UNKNOWN

    second = await reconciler.verify_by_task("TASK-1")
    assert second.status == ExecutionStatus.FAILED
    assert second.error.code == "ORDER_NOT_FOUND_CONFIRMED"
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.FAILED


@pytest.mark.anyio
async def test_remote_cancelled_order_is_failed(db):
    order = matched_gateway_order()
    order.raw["status"] = 5
    gateway = FakeGateway(order)
    reconciler, repository = await make_reconciler(db, gateway)

    result = await reconciler.verify_by_task("TASK-1")

    assert result.status == ExecutionStatus.FAILED
    assert result.error.code == "ORDER_CANCELLED_REMOTE"
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.FAILED


@pytest.mark.anyio
async def test_gateway_unavailable_stays_unknown(db):
    gateway = FakeGateway(None, unavailable=True)
    reconciler, repository = await make_reconciler(db, gateway)

    result = await reconciler.verify_by_task("TASK-1")

    assert result.status == ExecutionStatus.UNKNOWN
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.UNKNOWN
    assert record.verification_attempts == 1


@pytest.mark.anyio
async def test_manual_resolve_marks_success(db):
    gateway = FakeGateway(None)
    reconciler, repository = await make_reconciler(db, gateway)

    result = await reconciler.manual_resolve(
        idempotency_key="KEY-1",
        action="mark_succeeded",
        order_id="MANUAL-100",
        comment="人工确认 ERP 已创建",
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.SUCCEEDED
    assert record.order_id == "MANUAL-100"


@pytest.mark.anyio
async def test_worker_run_once_scans_unknown_records(db):
    from agent.procurement.reconciliation_worker import ReconciliationWorker

    gateway = FakeGateway(matched_gateway_order())
    reconciler, repository = await make_reconciler(db, gateway)
    await repository.mark_unknown(
        idempotency_key="KEY-1",
        code="ORDER_RESULT_UNKNOWN",
        message="等待对账",
        now=NOW,
    )
    worker = ReconciliationWorker(
        reconciler=reconciler,
        interval_seconds=1,
        batch_size=10,
    )

    stats = await worker.run_once()

    assert stats.scanned == 1
    assert stats.succeeded == 1
    record = await repository.get("KEY-1")
    assert record.status == ExecutionStatus.SUCCEEDED
