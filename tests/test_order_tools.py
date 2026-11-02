from datetime import date, datetime, timezone

import httpx
import pytest

from mcp_server.contracts import normalize_order_detail
from mcp_server.mcp_tools_bean import OrderCreateInput, OrderDetailItem
from mcp_server.tools.order_tools import (
    _prepare_create_payload,
    create_order_core,
)


class FakeRequest:
    headers = {}


class FakeRequestContext:
    def __init__(self, client):
        self.lifespan_context = {"http_client": client}
        self.request = FakeRequest()


class FakeContext:
    def __init__(self, client):
        self.request_context = FakeRequestContext(client)


def test_normalize_order_detail_preserves_expected_delivery_date():
    record = normalize_order_detail(
        {
            "partId": 65,
            "supplierId": 31,
            "orderTime": "2024-01-01T00:00:00",
            "expectedDeliveryDate": "2024-01-09",
            "actualDeliveryDate": None,
        }
    )

    assert record.expected_delivery_date == date(2024, 1, 9)
    assert record.actual_delivery_date is None


def test_prepare_create_payload_uses_business_timezone_date():
    payload = _prepare_create_payload(
        {}, now=datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)
    )

    # UTC 11-02 17:00 = 上海 11-03 01:00
    assert payload["orderTime"] == "2026-11-03T01:00:00.000"
    assert payload["orderNumber"].startswith("PO20261103")


@pytest.mark.anyio
async def test_create_order_returns_contract_order():
    async def handler(request):
        assert request.url.path == "/orders/create"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "orderId": 100,
                    "orderNumber": "P020261022ABC",
                    "status": 1,
                    "totalAmount": "20.00",
                    "orderDetail": [
                        {
                            "partId": 1,
                            "quantity": 2,
                            "unitPrice": "10.00",
                            "subtotal": "20.00",
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://test",
    ) as client:
        result = await create_order_core(
            OrderCreateInput(
                orderNumber="P020261022ABC",
                orderDetail=[
                    OrderDetailItem(
                        partId=1,
                        quantity=2,
                        unitPrice="10.00",
                    )
                ],
            ),
            FakeContext(client),
        )

    assert result.ok is True
    assert result.data["order_id"] == 100
    assert result.data["order_number"] == "P020261022ABC"
    assert result.meta.tool == "order_create"


@pytest.mark.anyio
async def test_get_order_by_number_uses_single_by_number_call():
    from mcp_server.tools.order_tools import get_order_by_number_core

    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        assert request.url.path == "/orders/get_by_number/P0BCF94565B4B2"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "id": 190,
                    "orderNumber": "P0BCF94565B4B2",
                    "status": 1,
                    "totalAmount": "58.00",
                    "orderDetail": [
                        {
                            "partId": 67,
                            "quantity": 1,
                            "unitPrice": "58.00",
                            "subtotal": "58.00",
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://test",
    ) as client:
        result = await get_order_by_number_core(
            "P0BCF94565B4B2", FakeContext(client)
        )

    assert result.ok is True
    assert result.data["order_id"] == 190
    assert len(result.data["details"]) == 1
    assert calls == [("GET", "/orders/get_by_number/P0BCF94565B4B2")]
