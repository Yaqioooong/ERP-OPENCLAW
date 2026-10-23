from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.analysis_calculator import (
    AnalysisInvariantError,
    DeterministicAnalyzer,
)
from agent.procurement.models import (
    CandidateSet,
    InventorySnapshot,
    ProcurementPlan,
    ProcurementRequest,
    RequestedItem,
    SupplierOffer,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


def make_inputs(*, inventory_warehouse: str = "A区"):
    request = ProcurementRequest(
        items=[RequestedItem(part_id=1, part_name="活塞环", quantity=10)],
        warehouse_location="A区",
        budget=Decimal("200.00"),
    )
    candidates = CandidateSet(
        offers=[
            SupplierOffer(
                supplier_id=4,
                supplier_name="SUP-4",
                supplier_status=1,
                part_id=1,
                part_name="活塞环",
                unit_price=Decimal("10.00"),
                price_source="part_master",
            )
        ],
        inventory=[
            InventorySnapshot(
                part_id=1,
                part_name="活塞环",
                warehouse_location=inventory_warehouse,
                current_quantity=7,
                safety_stock=0,
            )
        ],
    )
    plan = ProcurementPlan(
        plan_id="PLAN-1",
        task_id="TASK-1",
        version=1,
        supplier_id=4,
        supplier_name="SUP-4",
        warehouse_location="A区",
        items=[
            {
                "part_id": 1,
                "part_name": "活塞环",
                "quantity": 10,
                "unit_price": Decimal("10.00"),
                "subtotal": Decimal("100.00"),
                "price_source": "part_master",
            }
        ],
        total_amount=Decimal("100.00"),
        budget=Decimal("200.00"),
        created_at=NOW,
    )
    return request, candidates, plan


def test_calculator_computes_total_and_shortage():
    request, candidates, plan = make_inputs()
    result = DeterministicAnalyzer().analyze(
        task_id="TASK-1",
        request=request,
        candidates=candidates,
        plan=plan,
        now=NOW,
    )

    assert result.total_cost == Decimal("100.00")
    assert result.inventory_shortage == 3
    assert result.budget_delta == Decimal("100.00")
    assert result.lines[0].inventory_gap == 3


def test_calculator_does_not_use_other_warehouse_inventory():
    request, candidates, plan = make_inputs(inventory_warehouse="B区")
    result = DeterministicAnalyzer().analyze(
        task_id="TASK-1",
        request=request,
        candidates=candidates,
        plan=plan,
        now=NOW,
    )

    assert result.lines[0].available_quantity is None
    assert result.lines[0].inventory_gap is None
    assert result.inventory_shortage is None
    assert result.unknown_inventory_parts == [1]


def test_calculator_rejects_bad_subtotal():
    request, candidates, plan = make_inputs()
    plan.items[0].subtotal = Decimal("99.00")

    with pytest.raises(AnalysisInvariantError) as exc:
        DeterministicAnalyzer().analyze(
            task_id="TASK-1",
            request=request,
            candidates=candidates,
            plan=plan,
            now=NOW,
        )

    assert exc.value.code == "LINE_SUBTOTAL_MISMATCH"
