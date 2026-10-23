import random
import time
from datetime import date, datetime
from decimal import Decimal

from fastmcp import FastMCP, Context

from mcp_server.contracts import (
    ToolErrorCode,
    normalize_order,
    normalize_order_detail,
)
from mcp_server.mcp_tools_bean import (
    OrderCreateInput,
    OrderSearchInput,
    OrderUpdateInput,
)
from mcp_server.tool_io import failure, request_json, success

GROUP_NAME = "order"


def _generate_order_number() -> str:
    today = datetime.now().strftime("%Y%m%d")
    suffix = str(random.randint(0, 999)).zfill(3)
    return f"PO{today}{suffix}"


def _serialize_request(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_request(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_request(item) for key, item in value.items()}
    return value


def _prepare_create_payload(data: dict) -> dict:
    payload = dict(data)
    if not payload.get("orderNumber"):
        payload["orderNumber"] = _generate_order_number()
    if not payload.get("orderTime"):
        now = datetime.now()
        payload["orderTime"] = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{now.microsecond // 1000:03d}"
        )
    payload.setdefault("status", 1)
    return _serialize_request(payload)


async def create_order_core(create_data: OrderCreateInput, ctx: Context):
    started = time.monotonic()
    payload = _prepare_create_payload(
        create_data.model_dump(exclude_none=True)
    )
    data, error = await request_json(
        ctx=ctx,
        tool="order_create",
        method="POST",
        path="/orders/create",
        json_body=payload,
    )
    if error:
        return error
    if data is None:
        return failure(
            code=ToolErrorCode.UPSTREAM_UNAVAILABLE,
            message="ERP 创建订单成功但没有返回订单数据",
            tool="order_create",
            ctx=ctx,
            started=started,
            retryable=True,
        )
    return success(
        normalize_order(data).model_dump(mode="json"),
        tool="order_create",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def update_order_core(
        order_id: int,
        update_data: OrderUpdateInput,
        ctx: Context,
):
    started = time.monotonic()
    payload = _serialize_request(
        update_data.model_dump(exclude_none=True)
    )
    data, error = await request_json(
        ctx=ctx,
        tool="order_update",
        method="PUT",
        path=f"/orders/update/{order_id}",
        json_body=payload,
    )
    if error:
        return error
    if data is None:
        return success(
            None,
            tool="order_update",
            ctx=ctx,
            started=started,
            upstream_code=200,
        )
    return success(
        normalize_order(data).model_dump(mode="json"),
        tool="order_update",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def search_order_details_core(params: OrderSearchInput, ctx: Context):
    started = time.monotonic()
    query = params.model_dump(exclude_none=True)
    if "startDate" in query:
        query["startDate"] = query["startDate"].isoformat()
    if "endDate" in query:
        query["endDate"] = query["endDate"].isoformat()

    data, error = await request_json(
        ctx=ctx,
        tool="order_search_details",
        method="GET",
        path="/orders/search-details",
        params=query,
    )
    if error:
        return error
    records = [
        normalize_order_detail(item).model_dump(mode="json")
        for item in (data or [])
        if isinstance(item, dict)
    ]
    return success(
        records,
        tool="order_search_details",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def get_order_core(order_id: int, ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="order_get",
        method="GET",
        path=f"/orders/get/{order_id}",
        not_found_ok=True,
    )
    if error:
        return error
    payload = None if data is None else normalize_order(data).model_dump(mode="json")
    return success(
        payload,
        tool="order_get",
        ctx=ctx,
        started=started,
        upstream_code=404 if data is None else 200,
    )


async def get_order_by_number_core(order_number: str, ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="order_get_by_number",
        method="GET",
        path=f"/orders/get/{order_number}",
        not_found_ok=True,
    )
    if error:
        return error
    payload = None if data is None else normalize_order(data).model_dump(mode="json")
    return success(
        payload,
        tool="order_get_by_number",
        ctx=ctx,
        started=started,
        upstream_code=404 if data is None else 200,
    )


def register_order_tools(mcp: FastMCP):
    @mcp.tool(name=f"{GROUP_NAME}_create")
    async def create_order(create_data: OrderCreateInput, ctx: Context):
        return await create_order_core(create_data, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_update")
    async def update_order(
            order_id: int,
            update_data: OrderUpdateInput,
            ctx: Context,
    ):
        return await update_order_core(order_id, update_data, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_search_details")
    async def search_order_details(params: OrderSearchInput, ctx: Context):
        return await search_order_details_core(params, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_get")
    async def get_order(order_id: int, ctx: Context):
        return await get_order_core(order_id, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_get_by_number")
    async def get_order_by_number(order_number: str, ctx: Context):
        return await get_order_by_number_core(order_number, ctx)
