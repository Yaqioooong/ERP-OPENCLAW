import random
from datetime import datetime
from decimal import Decimal

import httpx
from fastmcp import FastMCP, Context

from mcp_server.mcp_tools_bean import OrderInput, OrderSearchInput

GROUP_NAME = "order"


# MemoryMiddleware

def _generate_order_number() -> str:
    """生成订单编号：PO + 年月日(8位) + 3位随机数字"""
    today = datetime.now().strftime("%Y%m%d")
    suffix = str(random.randint(0, 999)).zfill(3)
    return f"PO{today}{suffix}"


def _prepare_request(data: dict) -> dict:
    """填充默认值并序列化请求体：Decimal→float，date→ISO字符串"""
    # 自动生成 orderNumber
    if not data.get("orderNumber"):
        data["orderNumber"] = _generate_order_number()

    # 默认 orderTime（格式：yyyy-MM-ddTHH:mm:ss.SSS，匹配后端 CustomLocalDateTimeDeserializer）
    if not data.get("orderTime"):
        now = datetime.now()
        data["orderTime"] = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}"

    # 递归序列化
    return _serialize_request(data)


def _serialize_request(data: dict) -> dict:
    """递归处理：Decimal → float，date/datetime → ISO 字符串"""
    for key, value in data.items():
        if isinstance(value, Decimal):
            data[key] = float(value)
        elif hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _serialize_request(item)
        elif isinstance(value, dict):
            _serialize_request(value)
    return data


def _error_from_response(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except Exception:
        body = {}
    code = body.get("code")
    message = body.get("message") or response.text
    text = str(message)
    duplicate = (
            "已存在" in text
            or "重复" in text
            or "duplicate" in text.lower()
    )
    return {
        "error": {
            "code": "ORDER_DUPLICATE" if duplicate else "ERP_BUSINESS_ERROR",
            "duplicate": duplicate,
            "retryable": False,
            "message": text,
        }
    }


def register_order_tools(mcp: FastMCP):
    """注册订单分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_create")
    async def create_order(create_data: OrderInput, ctx: Context) -> dict:
        """
        创建采购订单（POST /orders/create）。

        orderNumber 不传则自动生成（规则：PO+年月日+3位随机数字）。
        orderTime 不传则默认当前时间（格式：yyyy-MM-ddTHH:mm:ss.SSS）。
        orderDetail 必填，至少包含一个明细项；每项需提供 partId、quantity、unitPrice。

        Args:
            create_data: 订单数据对象
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        request_data = create_data.model_dump(exclude_none=True)
        request_data = _prepare_request(request_data)

        try:
            print(request_data)
            response = await http_client.post(
                "/orders/create",
                json=request_data
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return {
                    "error": {
                        "code": "ERP_BUSINESS_ERROR",
                        "duplicate": False,
                        "retryable": False,
                        "message": f"code={result.get('code')}, message={result.get('message')}",
                    }
                }

            return result.get("data", {})
        except httpx.HTTPStatusError as e:
            return _error_from_response(e.response)
        except Exception as e:
            return {"error": {
                "code": "ERP_UNAVAILABLE",
                "duplicate": False,
                "retryable": True,
                "message": str(e),
            }}

    @mcp.tool(name=f"{GROUP_NAME}_update")
    async def update_order(order_id: int, update_data: OrderInput, ctx: Context) -> dict:
        """
        更新采购订单（PUT /orders/update/{id}）。

        orderDetail 为可选，传入则替换原有明细。
        其他字段与创建订单格式一致。

        Args:
            order_id: 订单 ID（路径参数，必填）
            update_data: 订单更新数据对象（只传需要变更的字段）
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        request_data = update_data.model_dump(exclude_none=True)
        request_data = _prepare_request(request_data)

        try:
            print(request_data)
            response = await http_client.put(
                f"/orders/update/{order_id}",
                json=request_data
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return {
                    "error": f"API error: code={result.get('code')}, message={result.get('message')}"
                }

            return result.get("data", {})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool(name=f"{GROUP_NAME}_search_details")
    async def search_order_details(params: OrderSearchInput, ctx: Context) -> list:
        """
        搜索采购订单明细。

        支持按零部件名称、日期范围筛选，所有参数均为可选。
        返回的每条明细包含零部件详情（partDetail）及供应商信息（supplier）。

        Args:
            params: 查询参数对象，partName、startDate、endDate 均为可选
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")
        request_params = params.model_dump(exclude_none=True)

        try:
            response = await http_client.get(
                "/orders/search-details",
                params=request_params
            )
            response.raise_for_status()
            result = response.json()

            if result.get("code") != 200:
                return [f"API error: code={result.get('code')}"]

            return result.get("data", [])
        except Exception as e:
            return [f'没有查询到任何信息，而且报错: {e}']

    @mcp.tool(name=f"{GROUP_NAME}_get")
    async def get_order(order_id: int, ctx: Context) -> dict:
        http_client = ctx.request_context.lifespan_context.get("http_client")
        try:
            response = await http_client.get(f"/orders/get/{order_id}")
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return _error_from_response(response)
            return result.get("data", {})
        except httpx.HTTPStatusError as e:
            return _error_from_response(e.response)
        except Exception as e:
            return {
                "error": {
                    "code": "ERP_UNAVAILABLE",
                    "duplicate": False,
                    "retryable": True,
                    "message": str(e),
                }
            }

    @mcp.tool(name=f"{GROUP_NAME}_get_by_number")
    async def get_order_by_number(order_number: str, ctx: Context) -> dict:
        http_client = ctx.request_context.lifespan_context.get("http_client")
        try:
            response = await http_client.get(
                f"/orders/get/{order_number}")
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 200:
                return _error_from_response(response)
            return result.get("data", {})
        except httpx.HTTPStatusError as exc:
            return _error_from_response(exc.response)
        except Exception as exc:
            return {
                "error": {
                    "code": "ERP_UNAVAILABLE",
                    "duplicate": False,
                    "retryable": True,
                    "message": str(exc),
                }
            }
