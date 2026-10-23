from decimal import Decimal

import pytest

from agent.procurement.deterministic_plan_proposer import DeterministicPlanProposer
from agent.procurement.models import (
    CandidateSet,
    ProcurementRequest,
    RequestedItem,
    SupplierOffer,
)


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
