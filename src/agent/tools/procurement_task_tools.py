"""主 Agent 可调用的采购任务工具。

方案 B：前端可以直接创建采购任务；主 Agent 也可以通过这些工具启动、
查询和恢复同一套 ProcurementTaskRunner，而不是绕回旧订单子 Agent。
"""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.procurement.dependencies import get_task_runner


def _context(runtime: ToolRuntime) -> tuple[str, str, tuple[str, ...]]:
    context = getattr(runtime, "context", None)
    user_id = getattr(context, "user_id", None) or "guoyaxing"
    username = getattr(context, "username", None) or user_id
    roles = tuple(getattr(context, "roles", ()) or ())
    return user_id, username, roles


def _dump(result: Any) -> str:
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    return json.dumps(data, ensure_ascii=False, default=str)


def _public_result(result: Any) -> dict[str, Any]:
    """给主 Agent 的精简结果；完整报告由前端任务面板单独读取。"""
    data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    interrupt = data.get("interrupt")
    if isinstance(interrupt, dict):
        summary = dict(interrupt.get("summary") or {})
        summary.pop("analysis_report", None)
        summary.pop("analysis_validation", None)
        interrupt = {**interrupt, "summary": summary}
    state_summary = dict(data.get("state_summary") or {})
    for key in ("analysis_report", "analysis_validation", "analysis_sandbox"):
        state_summary.pop(key, None)
    return {
        "status": data.get("status"),
        "task_id": data.get("task_id"),
        "thread_id": data.get("thread_id"),
        "interrupt": interrupt,
        "state_summary": state_summary,
        "recoverable": data.get("recoverable"),
    }


class StartProcurementTaskInput(BaseModel):
    request: dict[str, Any] = Field(
        ...,
        description=(
            "结构化采购需求。必须使用以下字段："
            "items[].part_id、items[].part_name、items[].quantity、"
            "items[].part_code、items[].target_unit_price；"
            "warehouse_location、budget、required_by、preferred_supplier_id、priority。"
            "不要使用 material_id/material_name/part 等别名；"
            "不要在模型侧编造 part_id/预算/仓库。"
        ),
    )


def _normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    """兼容模型常见的字段别名，最终统一为 ProcurementRequest 契约。"""
    data = dict(request or {})
    normalized_items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get("part_id") is None:
            item["part_id"] = item.get("material_id") or item.get("partId")
        if item.get("part_name") is None:
            item["part_name"] = (
                item.get("material_name")
                or item.get("partName")
                or item.get("name")
            )
        if item.get("part_code") is None:
            item["part_code"] = item.get("material_code") or item.get("partCode")
        if item.get("quantity") is None:
            item["quantity"] = item.get("qty")
        for alias in ("material_id", "material_name", "material_code", "part", "partId", "partName", "partCode", "name", "qty"):
            item.pop(alias, None)
        normalized_items.append(item)
    data["items"] = normalized_items
    if data.get("priority") not in {"price", "lead_time", "balanced"}:
        data["priority"] = "balanced"
    return data


class GetProcurementTaskInput(BaseModel):
    task_id: str = Field(..., description="采购任务 ID，例如 TASK-20261023-ABCD1234")


class ResumeProcurementTaskInput(BaseModel):
    task_id: str = Field(..., description="采购任务 ID")
    resume: dict[str, Any] = Field(
        ...,
        description=(
            "恢复数据。审批：{'decision':'approve'|'reject'|'modify',"
            "'changes': {...}}；重试：{'action':'retry'|'cancel'}。"
        ),
    )


async def start_procurement_task(
    request: dict[str, Any],
    runtime: ToolRuntime,
) -> str:
    """启动完整采购任务：分析 → 审批 → 订单执行 → 核验。"""
    user_id, username, roles = _context(runtime)
    runner = await get_task_runner()
    try:
        result = await runner.start_background(
            raw_request="",
            user_id=user_id,
            username=username,
            request=_normalize_request(request),
            roles=roles,
        )
        # 后台任务会通过 SSE 继续推进；前端收到 task_id 后打开采购任务面板。
        return _dump(_public_result(result))
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "error": type(exc).__name__,
                "message": str(exc),
            },
            ensure_ascii=False,
        )


async def get_procurement_task(
    task_id: str,
    runtime: ToolRuntime,
) -> str:
    """查询采购任务状态、审批中断和最终结果。"""
    user_id, _, roles = _context(runtime)
    runner = await get_task_runner()
    try:
        result = await runner.get(
            task_id=task_id,
            user_id=user_id,
            roles=roles,
        )
        return _dump(_public_result(result))
    except PermissionError:
        return json.dumps(
            {"status": "forbidden", "message": "无权查看该采购任务"},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {"status": "error", "message": str(exc)},
            ensure_ascii=False,
        )


async def resume_procurement_task(
    task_id: str,
    resume: dict[str, Any],
    runtime: ToolRuntime,
) -> str:
    """恢复采购任务：提交审批决定、补信息或重试。"""
    user_id, username, roles = _context(runtime)
    runner = await get_task_runner()
    try:
        result = await runner.resume(
            task_id=task_id,
            user_id=user_id,
            username=username,
            resume_data=resume,
            roles=roles,
        )
        return _dump(_public_result(result))
    except PermissionError:
        return json.dumps(
            {"status": "forbidden", "message": "无权操作该采购任务"},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {"status": "error", "message": str(exc)},
            ensure_ascii=False,
        )


def build_procurement_task_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=start_procurement_task,
            name="start_procurement_task",
            description=(
                "启动一笔完整的采购任务，依次执行需求分析、审批、下单和订单核验。"
                "适用于用户明确要求采购/下单，而不是只做查询或比价。"
                "返回 task_id，后续用 get_procurement_task/resume_procurement_task。"
            ),
            args_schema=StartProcurementTaskInput,
        ),
        StructuredTool.from_function(
            coroutine=get_procurement_task,
            name="get_procurement_task",
            description=(
                "查询采购任务状态、审批中断、执行结果和订单号。"
                "需要 task_id。"
            ),
            args_schema=GetProcurementTaskInput,
        ),
        StructuredTool.from_function(
            coroutine=resume_procurement_task,
            name="resume_procurement_task",
            description=(
                "恢复采购任务。审批用 {'decision':'approve'|'reject'|'modify'}；"
                "重试用 {'action':'retry'|'cancel'}。需要 task_id。"
            ),
            args_schema=ResumeProcurementTaskInput,
        ),
    ]
