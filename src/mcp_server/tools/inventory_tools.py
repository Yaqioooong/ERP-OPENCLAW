import time

from fastmcp import FastMCP, Context

from mcp_server.contracts import normalize_inventory
from mcp_server.mcp_tools_bean import InventorySearchInput
from mcp_server.tool_io import request_json, success

GROUP_NAME = "inventory"


async def list_inventory_warnings_core(ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="inventory_warning",
        method="GET",
        path="/inventory/warning",
    )
    if error:
        return error
    records = [
        normalize_inventory(item).model_dump(mode="json")
        for item in (data or [])
        if isinstance(item, dict)
    ]
    return success(
        records,
        tool="inventory_warning",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def list_inventory_page_core(params: InventorySearchInput, ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="inventory_page",
        method="GET",
        path="/inventory/page",
        params=params.model_dump(exclude_none=True),
    )
    if error:
        return error
    raw_records = data.get("records", []) if isinstance(data, dict) else (data or [])
    records = [
        normalize_inventory(item).model_dump(mode="json")
        for item in raw_records
        if isinstance(item, dict)
    ]
    payload = {
        "records": records,
        "total": data.get("total") if isinstance(data, dict) else len(records),
        "current": data.get("current") if isinstance(data, dict) else params.current,
        "size": data.get("size") if isinstance(data, dict) else params.size,
    }
    return success(
        payload,
        tool="inventory_page",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


def register_inventory_tools(mcp: FastMCP):
    @mcp.tool(name=f"{GROUP_NAME}_warning")
    async def list_inventory_warnings(ctx: Context):
        return await list_inventory_warnings_core(ctx)

    @mcp.tool(name=f"{GROUP_NAME}_page")
    async def list_inventory_page(params: InventorySearchInput, ctx: Context):
        return await list_inventory_page_core(params, ctx)
