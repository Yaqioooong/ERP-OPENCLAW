# tests/factories.py
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from agent.procurement.models import (
    ApprovalRecord,
    ApprovalStatus,
    CandidateSet,
    InventoryGap,
    InventorySnapshot,
    PlanItem,
    ProcurementPlan,
    ProcurementRequest,
    RequestedItem,
    SupplierOffer,
)
from agent.procurement.rules import q2

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=TZ)


def make_request(
    *,
    part_id: int = 1,
    part_name: str = "活塞环组件",
    quantity: int = 200,
    warehouse_location: str = "华东仓",
    budget: str | Decimal | None = "50000.00",
    required_by: date | None = date(2026, 11, 1),
    **overrides,
) -> ProcurementRequest:
    data = {
        "items": [
            RequestedItem(
                part_id=part_id,
                part_name=part_name,
                quantity=quantity,
            )
        ],
        "warehouse_location": warehouse_location,
        "budget": Decimal(str(budget)) if budget is not None else None,
        "required_by": required_by,
    }
    data.update(overrides)
    return ProcurementRequest(**data)


def make_plan(
    *,
    task_id: str = "TASK-1",
    version: int = 1,
    quantity: int = 200,
    unit_price: str | Decimal = "100.00",
    subtotal: str | Decimal | None = None,
    total: str | Decimal | None = None,
    rationale: str = "",
    supplier_id: int = 4,
    warehouse_location: str = "华东仓",
    expected_delivery_date: date | None = date(2026, 10, 28),
    created_at: datetime = NOW,
    **overrides,
) -> ProcurementPlan:
    price = Decimal(str(unit_price))
    line_subtotal = (
        Decimal(str(subtotal)) if subtotal is not None else q2(price * quantity)
    )
    plan_total = Decimal(str(total)) if total is not None else line_subtotal

    data = {
        "plan_id": f"PLAN-{task_id}-V{version}",
        "task_id": task_id,
        "version": version,
        "supplier_id": supplier_id,
        "supplier_name": "测试供应商",
        "warehouse_location": warehouse_location,
        "items": [
            PlanItem(
                part_id=1,
                part_name="活塞环组件",
                quantity=quantity,
                unit_price=price,
                subtotal=line_subtotal,
                price_source="historical_order",
                lead_time_days=5,
            )
        ],
        "total_amount": plan_total,
        "currency": "CNY",
        "expected_delivery_date": expected_delivery_date,
        "inventory_gaps": [
            InventoryGap(
                part_id=1,
                requested_quantity=quantity,
                available_quantity=50,
                gap=max(0, quantity - 50),
            )
        ],
        "budget": Decimal("50000.00"),
        "rationale": rationale,
        "created_at": created_at,
    }
    data.update(overrides)
    return ProcurementPlan(**data)


def make_candidates(
    *,
    supplier_id: int = 4,
    part_id: int = 1,
    part_name: str = "活塞环组件",
    unit_price: str | Decimal | None = "100.00",
    supplier_status: int | None = 1,
    lead_time_days: int | None = 5,
    available_quantity: int | None = 50,
    warehouse_location: str = "华东仓",
) -> CandidateSet:
    price = Decimal(str(unit_price)) if unit_price is not None else None
    return CandidateSet(
        offers=[
            SupplierOffer(
                supplier_id=supplier_id,
                supplier_name="测试供应商",
                supplier_status=supplier_status,
                part_id=part_id,
                part_name=part_name,
                unit_price=price,
                price_source="historical_order" if price is not None else None,
                lead_time_days=lead_time_days,
                available_quantity=available_quantity,
                credit_rating="A",
            )
        ],
        inventory=[
            InventorySnapshot(
                part_id=part_id,
                part_name=part_name,
                warehouse_location=warehouse_location,
                current_quantity=available_quantity,
                safety_stock=10,
            )
        ],
    )


def make_record(
    *,
    approval_id: str = "APR-TEST-001",
    task_id: str = "TASK-1",
    plan_version: int = 1,
    plan_hash: str = "hash-1",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    requested_by: str = "u1",
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        task_id=task_id,
        plan_version=plan_version,
        plan_hash=plan_hash,
        status=status,
        requested_by=requested_by,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )