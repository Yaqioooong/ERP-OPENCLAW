from fastmcp import FastMCP, Context

from mcp_server.mcp_tools_bean import SupplierQueryInput

# 分组名称
GROUP_NAME = "supplier"
def register_supplier_tools(mcp: FastMCP):
    """注册供应商分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    async def query_suppliers(params: SupplierQueryInput, ctx: Context) -> list:
        """
        按名称模糊搜索供应商。

        Args:
            params: 查询参数对象，name 为供应商名称（模糊查询，必填）
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        request_params = params.model_dump()
        try:
            response = await http_client.get("/suppliers/search", params=request_params)
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]
            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']