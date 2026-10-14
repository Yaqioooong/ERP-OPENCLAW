from fastmcp import FastMCP, Context

from mcp_server.mcp_tools_bean import PartQueryInput, PartSearchInput

# 分组名称
GROUP_NAME = "part"   # 分组名称，最终工具名称为 part_query / part_search
def register_parts_tools(mcp: FastMCP):
    """注册零部件分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_parts(params: PartQueryInput, ctx: Context) -> list:
        """
        分页查询零部件列表。
        支持按名称模糊查询、按分类筛选、按供应商ID筛选。

        Args:
            params: 查询参数对象，包含 current, size, name, category, supplierId
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        # 过滤掉 None 值参数
        request_params = params.model_dump(exclude_none=True)

        try:
            response = await http_client.get("/parts/page", params=request_params)
            response.raise_for_status()
            result = response.json()

            # 检查业务状态码
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            # 返回 data 字段，通常包含 records, total, current, size 等
            return result.get("data", {}).get("records", [])

        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_search")
    async def search_parts(name: str, ctx: Context) -> list:
        """
        按名称搜索零部件。
        与 part_query 不同，此接口直接搜索，name 为必填参数。

        Args:
            name: 零件名称（模糊查询），必填
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/parts/search", params={"name": name})
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            # data 为数组
            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_by_supplier")
    async def list_parts_by_supplier(supplier_id: int, ctx: Context) -> list:
        """
        根据供应商 ID 查询该供应商下有采购记录的零配件列表。

        Args:
            supplier_id: 供应商 ID（路径参数，必填）
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get(f"/parts/supplier/{supplier_id}")
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']