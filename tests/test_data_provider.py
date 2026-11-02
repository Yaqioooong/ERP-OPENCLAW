from datetime import date
from decimal import Decimal

import pytest

from agent.procurement.data_provider import (
    McpProcurementDataProvider,
    UpstreamUnavailable,
)
from agent.procurement.models import ProcurementRequest, RequestedItem
from agent.procurement.ports import PartResolutionError, SupplierResolutionError


class FakeTool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def ainvoke(self, payload):
        self.calls.append(payload)
        return self.result


class QueryAwareFakeTool(FakeTool):
    def __init__(self, results):
        super().__init__(None)
        self.results = results

    async def ainvoke(self, payload):
        self.calls.append(payload)
        return {"ok": True, "data": self.results.get(payload.get("name"), [])}


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
    assert offer.lead_time_source == "actual_delivery"
    assert offer.available_quantity == 50


@pytest.mark.anyio
async def test_collect_enriches_supplier_from_part_supplier_id():
    tools = {
        "part_search": FakeTool(
            [
                {
                    "id": 65,
                    "name": "电装DENSO火花塞 U27ETR",
                    "partCode": "SPARK-DENSO-U27ETR",
                    "purchasePrice": 32,
                    "supplierId": 31,
                }
            ]
        ),
        "inventory_page": FakeTool([]),
        "order_search_details": FakeTool([]),
    }
    supplier_tool = FakeTool(
        {
            "id": 31,
            "name": "电装配件专营店",
            "status": 1,
            "credit_rating": "A",
        }
    )
    tools["supplier_get"] = supplier_tool
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})
    request = ProcurementRequest(
        items=[
            RequestedItem(
                part_name="DENSO U27ETR 火花塞",
                quantity=10,
            )
        ],
        warehouse_location="华东仓",
        required_by=date(2026, 11, 10),
    )

    candidates = await provider.collect(request)

    offer = candidates.offers[0]
    assert supplier_tool.calls == [{"supplier_id": 31}]
    assert offer.part_id == 65
    assert offer.unit_price == Decimal("32.00")
    assert offer.price_source == "part_master"
    assert offer.supplier_name == "电装配件专营店"
    assert offer.supplier_status == 1
    assert offer.credit_rating == "A"


@pytest.mark.anyio
async def test_collect_uses_expected_delivery_when_actual_delivery_is_missing():
    tools = make_tools(
        order_details=[
            {
                "partId": 1,
                "partDetail": {"id": 1, "name": "活塞环组件"},
                "supplierId": 4,
                "unitPrice": 110,
                "orderTime": "2026-09-01T00:00:00",
                "expectedDeliveryDate": "2026-09-06T00:00:00",
                "actualDeliveryDate": None,
            }
        ]
    )
    tools["supplier_get"] = FakeTool(
        {"id": 4, "name": "测试供应商", "status": 1, "credit_rating": "A"}
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    offer = candidates.offers[0]
    assert offer.lead_time_days == 5
    assert offer.lead_time_source == "expected_delivery"
    assert any("预计到货日期" in warning for warning in offer.data_warnings)
    assert any("预计到货日期" in warning for warning in candidates.data_warnings)


@pytest.mark.anyio
async def test_historical_lead_time_uses_calendar_date_difference():
    tools = make_tools(
        order_details=[
            {
                "partId": 1,
                "partDetail": {"id": 1, "name": "活塞环组件"},
                "supplierId": 4,
                "unitPrice": 110,
                "orderTime": "2026-09-01T10:00:00",
                "actualDeliveryDate": "2026-09-09T09:00:00",
            }
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    offer = candidates.offers[0]
    assert offer.lead_time_days == 8
    assert offer.lead_time_source == "actual_delivery"


@pytest.mark.anyio
async def test_historical_lead_time_converts_aware_times_to_business_timezone():
    tools = make_tools(
        order_details=[
            {
                "partId": 1,
                "partDetail": {"id": 1, "name": "活塞环组件"},
                "supplierId": 4,
                "unitPrice": 110,
                # 上海：2026-09-01 23:30 → 2026-09-10 00:30
                "orderTime": "2026-09-01T15:30:00Z",
                "actualDeliveryDate": "2026-09-09T16:30:00Z",
            }
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    assert candidates.offers[0].lead_time_days == 9


@pytest.mark.anyio
async def test_historical_lead_time_keeps_naive_business_local_date_semantics():
    tools = make_tools(
        order_details=[
            {
                "partId": 1,
                "partDetail": {"id": 1, "name": "活塞环组件"},
                "supplierId": 4,
                "unitPrice": 110,
                "orderTime": "2026-09-01T23:30:00",
                "actualDeliveryDate": "2026-09-09T00:30:00",
            }
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    assert candidates.offers[0].lead_time_days == 8


@pytest.mark.anyio
async def test_historical_delivery_before_order_is_not_a_negative_lead_time():
    tools = make_tools(
        order_details=[
            {
                "partId": 1,
                "partDetail": {"id": 1, "name": "活塞环组件"},
                "supplierId": 4,
                "unitPrice": 110,
                "orderTime": "2026-09-09T10:00:00",
                "actualDeliveryDate": "2026-09-01T09:00:00",
            }
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    assert candidates.offers[0].lead_time_days is None
    assert candidates.offers[0].lead_time_source is None


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


@pytest.mark.anyio
async def test_resolve_part_by_name_returns_unique_business_identity():
    provider = McpProcurementDataProvider(
        make_tools(), active_supplier_statuses={1}
    )

    resolved = await provider.resolve_part(
        RequestedItem(part_name="活塞环组件", quantity=200)
    )

    assert resolved.id == 1
    assert resolved.name == "活塞环组件"
    assert resolved.code == "PC2001"


@pytest.mark.anyio
async def test_resolve_part_by_id_does_not_depend_on_name_search():
    part = {
        "id": 65,
        "name": "电装DENSO火花塞 U27ETR",
        "partCode": "SPARK-DENSO-U27ETR",
        "purchasePrice": 32,
        "supplierId": 31,
    }
    part_search = FakeTool([])
    part_get = FakeTool(part)
    provider = McpProcurementDataProvider(
        {"part_search": part_search, "part_get": part_get},
        active_supplier_statuses={1},
    )

    resolved = await provider.resolve_part(
        RequestedItem(
            part_id=65,
            part_name="DENSO U27ETR 火花塞",
            quantity=10,
        )
    )

    assert resolved.id == 65
    assert resolved.name == "电装DENSO火花塞 U27ETR"
    assert part_get.calls == [{"part_id": 65}]
    assert part_search.calls == []


@pytest.mark.anyio
async def test_resolve_part_by_id_rejects_conflicting_part_code():
    part = {
        "id": 65,
        "name": "电装DENSO火花塞 U27ETR",
        "partCode": "SPARK-DENSO-U27ETR",
    }
    provider = McpProcurementDataProvider(
        {"part_get": FakeTool(part)},
        active_supplier_statuses={1},
    )

    with pytest.raises(PartResolutionError) as exc_info:
        await provider.resolve_part(
            RequestedItem(
                part_id=65,
                part_name="DENSO U27ETR 火花塞",
                part_code="SPARK-DENSO-WRONG",
                quantity=10,
            )
        )

    assert exc_info.value.code == "PART_CODE_MISMATCH"


@pytest.mark.anyio
async def test_resolve_part_retries_with_distinctive_name_tokens():
    part = {
        "id": 65,
        "name": "电装DENSO火花塞 U27ETR",
        "partCode": "SPARK-DENSO-U27ETR",
        "supplierId": 31,
    }
    other_parts = [
        {
            "id": 66,
            "name": "博世BOSCH火花塞 FR7DC+",
            "partCode": "SPARK-BOSCH-FR7DC",
            "supplierId": 32,
        },
        {
            "id": 67,
            "name": "NGK铱金火花塞 Iridium IX CR9EIX",
            "partCode": "SPARK-NGK-CR9EIX",
            "supplierId": 33,
        },
    ]
    tool = QueryAwareFakeTool(
        {
            "DENSO U27ETR 火花塞": [],
            "DENSO": [part],
            "U27ETR": [part],
            "火花塞": [part, *other_parts],
        }
    )
    provider = McpProcurementDataProvider(
        {"part_search": tool}, active_supplier_statuses={1}
    )

    resolved = await provider.resolve_part(
        RequestedItem(part_name="DENSO U27ETR 火花塞", quantity=10)
    )

    assert resolved.id == 65
    assert resolved.name == "电装DENSO火花塞 U27ETR"
    assert [call["name"] for call in tool.calls] == [
        "DENSO U27ETR 火花塞",
        "U27ETR",
        "DENSO",
        "火花塞",
    ]


@pytest.mark.anyio
async def test_resolve_part_by_code_returns_unique_business_identity():
    provider = McpProcurementDataProvider(
        {
            "part_query": FakeTool(
                [{"id": 7, "name": "柴油滤清器", "partCode": "DF-27"}]
            )
        },
        active_supplier_statuses={1},
    )

    resolved = await provider.resolve_part(
        RequestedItem(part_code="DF-27", quantity=10)
    )

    assert resolved.id == 7
    assert resolved.name == "柴油滤清器"
    assert resolved.code == "DF-27"


@pytest.mark.anyio
async def test_resolve_part_reports_ambiguity_as_user_fixable():
    tools = make_tools()
    tools["part_search"] = FakeTool(
        [
            {"id": 1, "name": "活塞环组件", "partCode": "PC2001"},
            {"id": 2, "name": "活塞环组件", "partCode": "PC2002"},
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    with pytest.raises(PartResolutionError) as exc_info:
        await provider.resolve_part(
            RequestedItem(part_name="活塞环组件", quantity=200)
        )

    assert exc_info.value.code == "PART_AMBIGUOUS"
    assert [item["id"] for item in exc_info.value.candidates] == [1, 2]


@pytest.mark.anyio
async def test_resolve_part_distinguishes_not_found_from_upstream_failure():
    tools = make_tools()
    tools["part_search"] = FakeTool([])
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    with pytest.raises(PartResolutionError) as exc_info:
        await provider.resolve_part(
            RequestedItem(part_name="不存在的物料", quantity=1)
        )

    assert exc_info.value.code == "PART_NOT_FOUND"


@pytest.mark.anyio
async def test_collect_unwraps_langchain_mcp_content_blocks():
    import json

    tools = make_tools()
    tools["part_search"] = FakeTool(
        [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "ok": True,
                        "data": [
                            {
                                "id": 1,
                                "name": "活塞环组件",
                                "partCode": "PC2001",
                                "purchasePrice": 100,
                                "supplierId": 4,
                            }
                        ],
                        "error": None,
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    )
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    candidates = await provider.collect(make_request())

    assert candidates.offers[0].supplier_id == 4


@pytest.mark.anyio
async def test_resolve_supplier_name_requires_unambiguous_match():
    tools = {
        "supplier_query": FakeTool(
            [
                {"id": 31, "name": "电装配件专营店", "status": 1},
                {"id": 2, "name": "电装（上海）国际贸易有限公司", "status": 1},
            ]
        )
    }
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    with pytest.raises(SupplierResolutionError) as exc_info:
        await provider.resolve(supplier_id=None, supplier_name="DENSO")

    assert exc_info.value.code == "SUPPLIER_AMBIGUOUS"
    assert [item["id"] for item in exc_info.value.candidates] == [31, 2]


@pytest.mark.anyio
async def test_resolve_supplier_id_uses_supplier_detail():
    tools = {
        "supplier_get": FakeTool(
            {"id": 31, "name": "电装配件专营店", "status": 1}
        )
    }
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    resolved = await provider.resolve(supplier_id=31, supplier_name=None)

    assert resolved.id == 31
    assert resolved.name == "电装配件专营店"


@pytest.mark.anyio
async def test_resolve_unique_supplier_name():
    tools = {
        "supplier_query": FakeTool(
            [{"id": 31, "name": "电装配件专营店", "status": 1}]
        )
    }
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    resolved = await provider.resolve(
        supplier_id=None, supplier_name="电装配件专营店"
    )

    assert resolved.id == 31
    assert resolved.name == "电装配件专营店"


@pytest.mark.anyio
async def test_resolve_supplier_rejects_inactive_supplier():
    tools = {
        "supplier_get": FakeTool(
            {"id": 31, "name": "电装配件专营店", "status": 0}
        )
    }
    provider = McpProcurementDataProvider(tools, active_supplier_statuses={1})

    with pytest.raises(SupplierResolutionError) as exc_info:
        await provider.resolve(supplier_id=31, supplier_name=None)

    assert exc_info.value.code == "SUPPLIER_UNAVAILABLE"
