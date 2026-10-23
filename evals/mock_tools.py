from __future__ import annotations

import asyncio
import time
from typing import Any

from evals.models import FaultPlan


def _ok(data: Any) -> dict:
    return {
        "ok": True,
        "data": data,
        "error": None,
        "meta": {"tool": "mock", "request_id": "mock", "elapsed_ms": 0},
    }


def _err(code: str, message: str) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": code in {"TIMEOUT", "UPSTREAM_UNAVAILABLE"},
            "details": {},
        },
        "meta": {"tool": "mock", "request_id": "mock", "elapsed_ms": 0},
    }


class MockTool:
    def __init__(self, name: str, handler, faults: FaultPlan):
        self.name = name
        self._handler = handler
        self._faults = faults
        self.calls = 0

    async def ainvoke(self, payload: dict) -> dict:
        self.calls += 1
        faults = self._faults
        if faults.fail_once and self.calls > 1:
            faults = FaultPlan()
        if faults.asset_delay_ms:
            await asyncio.sleep(faults.asset_delay_ms / 1000)
        return await self._handler(payload, faults)


async def _part_search(payload, faults):
    name = str((payload or {}).get("name") or "")
    if "不存在" in name:
        return _ok([])
    if faults.part_mode == "timeout":
        raise TimeoutError("part_search timeout")
    if faults.part_mode == "error":
        return _err("UPSTREAM_UNAVAILABLE", "part service error")
    if faults.part_mode == "not_found":
        return _ok([])
    price = None if faults.part_mode == "missing_price" else 100.0
    return _ok([
        {
            "id": 1,
            "name": "活塞环组件",
            "code": "PC2001",
            "purchasePrice": price,
            "supplierId": 4,
        },
        {
            "id": 2,
            "name": "火花塞",
            "code": "SP2001",
            "purchasePrice": 20.0,
            "supplierId": 4,
        },
    ])


async def _inventory_page(payload, faults):
    if faults.inventory_mode == "timeout":
        raise TimeoutError("inventory timeout")
    if faults.inventory_mode == "unknown":
        return _ok({"records": []})
    return _ok({
        "records": [
            {
                "partId": 1,
                "currentQuantity": 50,
                "safetyStock": 10,
                "warehouseLocation": "华东仓",
            },
            {
                "partId": 2,
                "currentQuantity": 50,
                "safetyStock": 10,
                "warehouseLocation": "华东仓",
            },
        ]
    })


async def _order_history(payload, faults):
    if faults.part_mode == "missing_price":
        return _ok([])
    if faults.supplier_mode == "timeout":
        raise TimeoutError("order history timeout")
    supplier_status = 2 if faults.supplier_mode == "inactive" else 1
    return _ok([
        {
            "partId": 1,
            "partDetail": {"id": 1, "name": "活塞环组件"},
            "supplier": {
                "id": 4,
                "name": "测试供应商",
                "status": supplier_status,
            },
            "unitPrice": 100.0,
            "orderTime": "2026-09-01T00:00:00",
            "actualDeliveryDate": "2026-09-06T00:00:00",
        },
        {
            "partId": 2,
            "partDetail": {"id": 2, "name": "火花塞"},
            "supplier": {
                "id": 4,
                "name": "测试供应商",
                "status": supplier_status,
            },
            "unitPrice": 20.0,
            "orderTime": "2026-09-01T00:00:00",
            "actualDeliveryDate": "2026-09-04T00:00:00",
        },
    ])


async def _part_get(payload, faults):
    return _ok({
        "id": 1,
        "name": "活塞环组件",
        "code": "PC2001",
        "purchasePrice": 100.0,
        "supplierId": 4,
    })


async def _supplier_get(payload, faults):
    return _ok({
        "id": 4,
        "name": "测试供应商",
        "status": 1,
        "credit_rating": "A",
    })


def build_mock_tools(faults: FaultPlan) -> dict[str, MockTool]:
    return {
        "part_search": MockTool("part_search", _part_search, faults),
        "part_get": MockTool("part_get", _part_get, faults),
        "inventory_page": MockTool("inventory_page", _inventory_page, faults),
        "order_search_details": MockTool(
            "order_search_details", _order_history, faults
        ),
        "supplier_get": MockTool("supplier_get", _supplier_get, faults),
    }
