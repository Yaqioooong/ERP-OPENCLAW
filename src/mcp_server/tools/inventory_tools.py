from fastmcp import FastMCP, Context
from openai import http_client

from mcp_server.mcp_tools_bean import InventorySearchInput

GROUP_NAME = "inventory"


def register_inventory_tools(mcp: FastMCP):
    """注册库存管理分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_warning")
    async def list_inventory_warnings(ctx: Context) -> list:
        """
        查询库存预警列表。
        返回所有库存不足（当前库存低于安全库存）的物料及对应的零部件详情。

        无需传参。
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/inventory/warning")
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_page")
    async def list_inventory_page(
            params: InventorySearchInput, ctx: Context
    ) -> list:
        """
        分页查询零件库存

        Args:
            param: 查询条件对象

        Returns:
            分页库存数据JSON, 包含currentQuantity,safeStock, warehouseLocation, partDetail
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        request_data = params.model_dump(exclude_none=True)

        try:
            print(request_data)
            response = await http_client.post(
                "/inventory/page",
                json=request_data,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}, message={result.get('message')}"]
            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']
