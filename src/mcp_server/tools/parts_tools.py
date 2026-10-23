from fastmcp import FastMCP, Context

from mcp_server.contracts import ToolErrorCode, normalize_part
from mcp_server.mcp_tools_bean import PartQueryInput
from mcp_server.tool_io import failure, request_json, success

GROUP_NAME = "part"


async def search_parts_core(name: str, ctx: Context):
    import time

    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="part_search",
        method="GET",
        path="/parts/search",
        params={"name": name},
    )
    if error:
        return error
    records = [
        normalize_part(item).model_dump(mode="json")
        for item in (data or [])
        if isinstance(item, dict)
    ]
    return success(
        records,
        tool="part_search",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def query_parts_core(params: PartQueryInput, ctx: Context):
    import time

    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="part_query",
        method="GET",
        path="/parts/page",
        params=params.model_dump(exclude_none=True),
    )
    if error:
        return error
    raw_records = data.get("records", []) if isinstance(data, dict) else (data or [])
    records = [
        normalize_part(item).model_dump(mode="json")
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
        tool="part_query",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def get_part_core(part_id: int, ctx: Context):
    import time

    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="part_get",
        method="GET",
        path=f"/parts/get/{part_id}",
        not_found_ok=False,
    )
    if error:
        return error
    if data is None:
        return failure(
            code=ToolErrorCode.NOT_FOUND,
            message=f"零件不存在: {part_id}",
            tool="part_get",
            ctx=ctx,
            started=started,
            details={"part_id": part_id},
            upstream_code=404,
        )
    return success(
        normalize_part(data).model_dump(mode="json"),
        tool="part_get",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


async def list_parts_by_supplier_core(supplier_id: int, ctx: Context):
    import time

    started = time.monotonic()
    data, error = await request_json(
        ctx=ctx,
        tool="part_by_supplier",
        method="GET",
        path=f"/parts/supplier/{supplier_id}",
    )
    if error:
        return error
    records = [
        normalize_part(item).model_dump(mode="json")
        for item in (data or [])
        if isinstance(item, dict)
    ]
    return success(
        records,
        tool="part_by_supplier",
        ctx=ctx,
        started=started,
        upstream_code=200,
    )


def register_parts_tools(mcp: FastMCP):
    @mcp.tool(name=f"{GROUP_NAME}_search")
    async def search_parts(name: str, ctx: Context):
        return await search_parts_core(name, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_parts(params: PartQueryInput, ctx: Context):
        return await query_parts_core(params, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_get")
    async def get_part(part_id: int, ctx: Context):
        return await get_part_core(part_id, ctx)

    @mcp.tool(name=f"{GROUP_NAME}_by_supplier")
    async def list_parts_by_supplier(supplier_id: int, ctx: Context):
        return await list_parts_by_supplier_core(supplier_id, ctx)
