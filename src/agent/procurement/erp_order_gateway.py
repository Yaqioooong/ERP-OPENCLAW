import json
from dataclasses import dataclass
from typing import Any, Protocol

from agent.procurement.models import ProcurementPlan


class GatewayTimeout(RuntimeError):
    """订单请求超时：结果可能已成功。"""


class GatewayUnavailable(RuntimeError):
    """订单服务暂时不可用/返回不可解析结果。"""


class GatewayBusinessError(RuntimeError):
    """订单服务明确拒绝；duplicate=True 表示订单号已存在。"""

    def __init__(self, message: str, *, code: str, duplicate: bool = False):
        super().__init__(message)
        self.code = code
        self.duplicate = duplicate


@dataclass(frozen=True)
class GatewayOrder:
    order_id: str
    order_number: str
    raw: dict[str, Any]


class ErpOrderGateway(Protocol):
    async def create_order(
            self,
            *,
            order_number: str,
            plan: ProcurementPlan,
            idempotency_key: str
    ) -> GatewayOrder: ...

    async def get_order_by_number(self, order_number: str) -> GatewayOrder | None: ...

    async def get_order_by_id(self, order_id: str) -> GatewayOrder | None: ...


class McpErpOrderGateway:
    """通过MCP工具访问JAVA ERP 订单接口"""

    def __init__(self, tools: dict[str, Any]):
        self._tools = tools

    async def create_order(
            self,
            *,
            order_number: str,
            plan: ProcurementPlan,
            idempotency_key: str
    ) -> GatewayOrder:
        payload = {
            "create_data": {
                "orderNumber": order_number,
                "orderDetail": [
                    {
                        "partId": item.part_id,
                        "quantity": item.quantity,
                        "unitPrice": float(item.unit_price),
                        "subtotal": float(item.subtotal),
                        "remark": f"plan:{plan.version}",
                    }
                    for item in plan.items
                ],
                "totalAmount": float(plan.total_amount),
                "exceptedDeliverDate": (
                    plan.expected_delivery_date.isoformat()
                    if plan.expected_delivery_date
                    else None
                ),
                "remark": f"task:{plan.task_id};idempotency:{idempotency_key}"
            }
        }
        result = await self._call("order_create", payload)
        return self._extract_order(result, fallback_number=order_number)

    async def get_order_by_number(self, order_number: str) -> GatewayOrder | None:
        result = await self._call(
            "order_get_by_number", {"order_number": order_number}
        )
        if result is None:
            return None
        return self._extract_order(result, fallback_number=order_number)

    async def get_order_by_id(self, order_id: str) -> GatewayOrder | None:
        result = await self._call("order_get", {"order_id": int(order_id)})
        if result is None:
            return None
        return self._extract_order(result, fallback_number=None)

    async def _call(self, name: str, payload: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise GatewayUnavailable(f"MCP 工具缺失: {name}")
        try:
            result = await tool.ainvoke(payload)
        except TimeoutError as exc:
            raise GatewayTimeout(str(exc)) from exc
        except Exception as exc:
            raise GatewayUnavailable(str(exc)) from exc

        if isinstance(result, str):
            text = result.strip()
            if not text or text.startswith("没有查询到"):
                return None
            try:
                result = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GatewayUnavailable(text) from exc

        if isinstance(result, dict) and result.get("error") is not None:
            self._raise_business_error(result["error"])

        if isinstance(result, dict) and "data" in result:
            return result["data"]

        return result

    @staticmethod
    def _raise_business_error(error: Any) -> None:
        if isinstance(error, dict):
            code = str(error.get("code") or "ERP_BUSINESS_ERROR")
            message = str(error.get("message") or error)

            if code == "ERP_TIMEOUT":
                raise GatewayTimeout(message)
            if code == "ERP_UNAVAILABLE":
                raise GatewayUnavailable(message)

            duplicate = bool(
                error.get("duplicate")
                or "DUPLICATE" in code.upper()
                or "已存在" in message
                or "重复" in message
            )
            raise GatewayBusinessError(
                message, code=code, duplicate=duplicate
            )

        text = str(error)
        duplicate = "已存在" in text or "重复" in text or "DUPLICATE" in text.upper()
        raise GatewayBusinessError(
            text, code="ERP_BUSINESS_ERROR", duplicate=duplicate
        )

    @staticmethod
    def _extract_order(
            result: Any,
            *,
            fallback_number: str | None,
    ) -> GatewayOrder:
        if not isinstance(result, dict):
            raise GatewayUnavailable(f"订单响应不是对象: {result!r}")

        order_id = result.get("orderId") or result.get("id")
        order_number = result.get("orderNumber") or fallback_number
        if order_id is None or order_number is None:
            raise GatewayUnavailable(
                f"订单响应缺少 orderId/orderNumber: {result!r}"
            )
        return GatewayOrder(
            order_id=str(order_id),
            order_number=str(order_number),
            raw=result,
        )
