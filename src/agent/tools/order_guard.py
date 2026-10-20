"""采购订单工具的确定性校验与 HITL 守卫。

模型负责提取订单信息，但不能负责决定“不完整订单是否可以落库”。
本模块在 MCP 工具真正执行前校验订单草稿，缺字段时直接 interrupt，
避免模型猜测数量、物料 ID 或单价后继续调用下单接口。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt


def _as_dict(value: Any) -> dict[str, Any]:
    """将 Pydantic 对象或普通映射统一成字典。"""
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return {}


def _missing_create_fields(create_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    details = create_data.get("orderDetail")

    if not isinstance(details, list) or not details:
        return ["orderDetail（至少一条采购明细）"]

    for index, item in enumerate(details, start=1):
        detail = _as_dict(item)
        prefix = f"第 {index} 条明细"
        if detail.get("partId") in (None, ""):
            missing.append(f"{prefix}的 partId（零部件 ID）")
        if detail.get("quantity") in (None, ""):
            missing.append(f"{prefix}的 quantity（采购数量）")
        elif isinstance(detail.get("quantity"), (int, float)) and detail["quantity"] < 1:
            missing.append(f"{prefix}的 quantity（必须大于 0）")
        if detail.get("unitPrice") in (None, ""):
            missing.append(f"{prefix}的 unitPrice（采购单价）")

    return missing


def _missing_update_fields(order_id: Any, update_data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if order_id in (None, ""):
        missing.append("order_id（待修改订单 ID）")

    if not update_data:
        missing.append("update_data（至少一个需要修改的字段）")

    details = update_data.get("orderDetail")
    if details is not None:
        if not isinstance(details, list) or not details:
            missing.append("orderDetail（明细不能为空）")
        else:
            for index, item in enumerate(details, start=1):
                detail = _as_dict(item)
                prefix = f"第 {index} 条明细"
                for field, label in (
                    ("partId", "零部件 ID"),
                    ("quantity", "采购数量"),
                    ("unitPrice", "采购单价"),
                ):
                    if detail.get(field) in (None, ""):
                        missing.append(f"{prefix}的 {field}（{label}）")
                if isinstance(detail.get("quantity"), (int, float)) and detail["quantity"] < 1:
                    missing.append(f"{prefix}的 quantity（必须大于 0）")

    return missing


def _interrupt_for_missing(
    operation: str,
    missing_fields: list[str],
    collected_data: dict[str, Any],
) -> Any:
    """统一生成前端已有协议可识别的订单补充中断。"""
    return interrupt(
        {
            "type": "order_info_request",
            "operation": operation,
            "missing_fields": "；".join(missing_fields),
            "collected_data": json.dumps(collected_data, ensure_ascii=False, default=str),
        }
    )


def _resume_result(operation: str, resume_data: Any) -> str:
    """把人工补充结果交回模型，让模型重新生成完整订单草稿。"""
    return json.dumps(
        {
            "status": "supplement_received",
            "operation": operation,
            "supplement": resume_data,
            "message": "已收到人工补充信息。请将补充信息合并到完整订单草稿后重新调用原订单工具；在字段完整前不要猜测或执行下单。",
        },
        ensure_ascii=False,
        default=str,
    )


def _approval_interrupt(operation: str, data: dict[str, Any]) -> Any:
    """在字段校验通过后请求人工确认，保持前端现有 HITL 协议。"""
    return interrupt(
        {
            "action_requests": [
                {
                    "name": f"order_{operation}",
                    "args": data,
                    "description": "订单字段已完整，请确认是否执行该采购订单操作。",
                }
            ],
            "review_configs": [
                {
                    "action_name": f"order_{operation}",
                    "allowed_decisions": ["approve", "reject"],
                }
            ],
        }
    )


def _approval_result(operation: str, decision: Any) -> str | None:
    """处理恢复后的审批结果；返回 None 表示允许继续执行。"""
    decisions = decision.get("decisions", []) if isinstance(decision, dict) else []
    first = decisions[0] if decisions else {}
    decision_type = first.get("type") if isinstance(first, dict) else None
    if decision_type == "approve":
        return None
    if decision_type == "reject":
        return json.dumps(
            {
                "status": "rejected",
                "operation": operation,
                "message": "人工拒绝了本次订单操作，未调用 ERP 接口。",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "awaiting_decision",
            "operation": operation,
            "message": "未收到有效审批结果，请重新确认 approve 或 reject。",
        },
        ensure_ascii=False,
    )


def build_guarded_order_tools(order_tools: list[Any]) -> list[Any]:
    """替换 order_create/order_update，其他订单查询工具保持原样。"""
    guarded: list[Any] = []

    for original_tool in order_tools:
        if original_tool.name not in {"order_create", "order_update"}:
            guarded.append(original_tool)
            continue

        if original_tool.name == "order_create":

            async def guarded_create(*, create_data: dict[str, Any], _tool=original_tool):
                data = _as_dict(create_data)
                missing = _missing_create_fields(data)
                if missing:
                    resumed = _interrupt_for_missing("create", missing, data)
                    return _resume_result("create", resumed)
                decision = _approval_interrupt("create", data)
                approval_result = _approval_result("create", decision)
                if approval_result is not None:
                    return approval_result
                return await _tool.ainvoke({"create_data": data})

            guarded.append(
                StructuredTool.from_function(
                    coroutine=guarded_create,
                    name="order_create",
                    description=(
                        "创建采购订单。执行前必须提供完整 orderDetail；每条明细必须包含 "
                        "partId、quantity、unitPrice。缺少任一字段时系统会自动请求人工补充，禁止猜测。"
                    ),
                    args_schema=_create_schema(),
                )
            )
        else:

            async def guarded_update(
                *, order_id: int | None, update_data: dict[str, Any], _tool=original_tool
            ):
                data = _as_dict(update_data)
                missing = _missing_update_fields(order_id, data)
                if missing:
                    resumed = _interrupt_for_missing("update", missing, {"order_id": order_id, **data})
                    return _resume_result("update", resumed)
                approval_data = {"order_id": order_id, "update_data": data}
                decision = _approval_interrupt("update", approval_data)
                approval_result = _approval_result("update", decision)
                if approval_result is not None:
                    return approval_result
                return await _tool.ainvoke({"order_id": order_id, "update_data": data})

            guarded.append(
                StructuredTool.from_function(
                    coroutine=guarded_update,
                    name="order_update",
                    description=(
                        "修改采购订单。必须提供 order_id 和至少一个完整修改字段；" 
                        "如果传入 orderDetail，每条明细必须包含 partId、quantity、unitPrice。"
                    ),
                    args_schema=_update_schema(),
                )
            )

    return guarded


def _create_schema():
    from pydantic import BaseModel, Field

    class CreateSchema(BaseModel):
        create_data: dict[str, Any] = Field(
            ..., description="采购订单草稿。orderDetail 必须包含 partId、quantity、unitPrice。"
        )

    return CreateSchema


def _update_schema():
    from pydantic import BaseModel, Field

    class UpdateSchema(BaseModel):
        order_id: int | None = Field(None, description="待修改的订单 ID")
        update_data: dict[str, Any] = Field(..., description="需要修改的订单字段")

    return UpdateSchema
