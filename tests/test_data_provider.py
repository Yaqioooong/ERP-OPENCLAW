from datetime import date
from decimal import Decimal

import pytest

from agent.procurement.data_provider import (
    McpProcurementDataProvider,
    UpstreamUnavailable,
)
from agent.procurement.models import ProcurementRequest, RequestedItem


class FakeTool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def ainvoke(self, payload):
        self.calls.append(payload)
        return self.result


def make_request() -> ProcurementRequest:
    return ProcurementRequest(
        items=[RequestedItem(part_name="活塞环组件", quantity=200)],
        warehouse_location="华东仓",
        budget=Decimal("50000.00"),
        required_by=date(2026, 11, 1),
    )


def make_tools(order_details=None, inventory=None):
    return {
        "part_search": FakeTool(
            [
                {
                    "id": 1,
                    "name": "活塞环组件",
                    "partCode": "PC2001",
                    "purchasePrice": 100,
                    "supplierId": 4,
                }
            ]
        ),
        "inventory_page": FakeTool(
            inventory
            if inventory is not None
            else [
                {
                    "partId": 1,
                    "currentQuantity": 50,
                    "safetyStock": 10,
                    "warehouseLocation": "华东仓",
                }
            ]
        ),
        "order_search_details": FakeTool(
            order_details
            if order_details is not None
            else [
                {
                    "partId": 1,
                    "partDetail": {"id": 1, "name": "活塞环组件"},
                    "supplier": {"id": 4, "name": "测试供应商", "status": 1},
                    "unitPrice": 110,
                    "orderTime": "2026-09-01T00:00:00",
                    "actualDeliveryDate": "2026-09-06T00:00:00",
                }
            ]
        ),
    }


@pytest.mark.anyio
async def test_collect_normalizes_history_price_and_lead_time():
    provider = McpProcurementDataProvider(
        make_tools(), active_supplier_statuses={1}
    )

    candidates = await provider.collect(make_request())

    assert len(candidates.inventory) == 1
    assert candidates.inventory[0].current_quantity == 50
    assert len(candidates.offers) == 1
    offer = candidates.offers[0]
    assert offer.supplier_id == 4
    assert offer.supplier_name == "测试供应商"
    assert offer.unit_price == Decimal("110.00")
    assert offer.price_source == "historical_order"
    assert offer.lead_time_days == 5
    assert offer.available_quantity == 50


@pytest.mark.anyio
async def test_collect_falls_back_to_part_master_price():
    provider = McpProcurementDataProvider(
        make_tools(order_details=[]), active_supplier_statuses={1}
    )

    candidates = await provider.collect(make_request())

    offer = candidates.offers[0]
    assert offer.unit_price == Decimal("100.00")
    assert offer.price_source == "part_master"
    assert offer.lead_time_days is None


@pytest.mark.anyio
async def test_collect_marks_unknown_inventory_instead_of_zero():
    provider = McpProcurementDataProvider(
        make_tools(inventory=[]), active_supplier_statuses={1}
    )

    candidates = await provider.collect(make_request())

    assert candidates.inventory[0].current_quantity is None
    assert candidates.offers[0].available_quantity is None


@pytest.mark.anyio
async def test_collect_rejects_ambiguous_part():
    tools = make_tools()
    tools["part_search"] = FakeTool(
        [
            {"id": 1, "name": "活塞环组件", "purchasePrice": 100, "supplierId": 4},
            {"id": 2, "name": "活塞环组件", "purchasePrice": 120, "supplierId": 5},
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    with pytest.raises(UpstreamUnavailable) as exc_info:
        await provider.collect(make_request())

    assert exc_info.value.code == "PART_AMBIGUOUS"
