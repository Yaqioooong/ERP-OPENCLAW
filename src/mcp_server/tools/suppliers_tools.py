import time

from fastmcp import FastMCP, Context

from mcp_server.contracts import ToolErrorCode, normalize_supplier
from mcp_server.mcp_tools_bean import SupplierQueryInput
from mcp_server.tool_io import failure, request_json, success

GROUP_NAME = "supplier"


async def query_suppliers_core(params: SupplierQueryInput, ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="supplier_query",
        method="GET",
        path="/suppliers/search",
        params=params.model_dump(),
    )
    if error:
        return error
    records = [
        normalize_supplier(item).model_dump(mode="json")
        for item in (data or [])
        if isinstance(item, dict)
    ]
    return success(
        records,
        tool="supplier_query",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def get_supplier_core(supplier_id: int, ctx: Context):
    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="supplier_get",
        method="GET",
        path=f"/suppliers/get/{supplier_id}",
    )
    if error:
        return error
    if data is None:
        return failure(
            code=ToolErrorCode.NOT_FOUND,
            message=f"供应商不存在: {supplier_id}",
            tool="supplier_get",
            ctx=ctx,
            started=started,
            details={"supplier_id": supplier_id},
            upstream_code=404,
        )
    return success(
        normalize_supplier(data).model_dump(mode="json"),
        tool="supplier_get",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


def register_supplier_tools(mcp: FastMCP):
    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_suppliers(params: SupplierQueryInput, ctx: Context):
        return await query_suppliers_core(params, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_get")
    async def get_supplier(supplier_id: int, ctx: Context):
        return await get_supplier_core(supplier_id, ctx)
