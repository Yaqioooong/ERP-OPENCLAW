from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.deterministic_plan_proposer import DeterministicPlanProposer
from agent.procurement.models import (
    CandidateSet,
    InventorySnapshot,
    ProcurementRequest,
    RequestedItem,
    SupplierOffer,
)
from agent.procurement.planner import DefaultPlanBuilder
from agent.procurement.rules import validate_plan


@pytest.mark.anyio
async def test_proposer_handles_single_complete_supplier():
    request = ProcurementRequest(
        items=[RequestedItem(part_id=67, part_name="火花塞", quantity=1)],
        warehouse_location="C区-3号库",
        budget=Decimal("100.00"),
    )
    candidates = CandidateSet(
        offers=[
            SupplierOffer(
                supplier_id=33,
                supplier_name="SUP-33",
                supplier_status=None,
                part_id=67,
                part_name="火花塞",
                unit_price=Decimal("58.00"),
                price_source="part_master",
            )
        ]
    )

    proposal = await DeterministicPlanProposer().propose(
        request, candidates, feedback=[]
    )

    assert proposal.supplier_id == 33
    assert proposal.items[0].part_id == 67


@pytest.mark.anyio
async def test_denso_plan_uses_expected_historical_lead_time_to_meet_required_date():
    tz = ZoneInfo("Asia/Shanghai")
    request = ProcurementRequest(
        items=[
            RequestedItem(
                part_id=65,
                part_name="电装DENSO火花塞 U27ETR",
                quantity=10,
            )
        ],
        warehouse_location="华东仓",
        budget=None,
        required_by=date(2026, 11, 10),
        preferred_supplier_id=31,
    )
    candidates = CandidateSet(
        offers=[
            SupplierOffer(
                supplier_id=31,
                supplier_name="电装配件专营店",
                supplier_status=1,
                part_id=65,
                part_name="电装DENSO火花塞 U27ETR",
                unit_price=Decimal("32.00"),
                price_source="part_master",
                lead_time_days=8,
                lead_time_source="expected_delivery",
                available_quantity=20,
                credit_rating="A",
            )
        ],
        inventory=[
            InventorySnapshot(
                part_id=65,
                part_name="电装DENSO火花塞 U27ETR",
                warehouse_location="华东仓",
                current_quantity=20,
                safety_stock=20,
            )
        ],
    )
    proposal = await DeterministicPlanProposer().propose(
        request, candidates, feedback=[]
    )
    plan = DefaultPlanBuilder().build(
        task_id="TASK-DENSO",
        version=1,
        request=request,
        candidates=candidates,
        proposal=proposal,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=tz),
    )

    result = validate_plan(
        plan,
        request,
        candidates,
        task_id="TASK-DENSO",
        today=date(2026, 11, 2),
    )

    assert plan.expected_delivery_date == date(2026, 11, 10)
    assert plan.items[0].lead_time_source == "expected_delivery"
    assert result.passed is True


@pytest.mark.anyio
async def test_plan_builder_uses_business_date_for_aware_now():
    request = ProcurementRequest(
        items=[RequestedItem(part_id=65, part_name="火花塞", quantity=10)],
        warehouse_location="华东仓",
        required_by=date(2026, 11, 11),
    )
    candidates = CandidateSet(
        offers=[
            SupplierOffer(
                supplier_id=31,
                supplier_name="测试供应商",
                part_id=65,
                part_name="火花塞",
                unit_price=Decimal("32.00"),
                price_source="part_master",
                lead_time_days=8,
                lead_time_source="actual_delivery",
            )
        ]
    )
    proposal = await DeterministicPlanProposer().propose(
        request, candidates, feedback=[]
    )

    plan = DefaultPlanBuilder().build(
        task_id="TASK-TZ",
        version=1,
        request=request,
        candidates=candidates,
        proposal=proposal,
        # UTC 11-02 17:00 = 上海 11-03 01:00
        now=datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc),
    )

    assert plan.expected_delivery_date == date(2026, 11, 11)
