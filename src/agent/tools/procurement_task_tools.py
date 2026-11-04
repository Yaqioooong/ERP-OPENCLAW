"""主 Agent 可调用的采购任务工具。

方案 B：前端可以直接创建采购任务；主 Agent 也可以通过这些工具启动、
查询和恢复同一套 ProcurementTaskRunner，而不是绕回旧订单子 Agent。
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.memory.identity import require_user_id
from agent.procurement.dependencies import get_task_runner
from agent.procurement.request_normalization import (
    ProcurementRequestInputError,
    normalize_procurement_request,
)


_PART_MODEL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9._/+~-]*[A-Za-z])"
    r"(?=[A-Za-z0-9._/+~-]*\d)[A-Za-z0-9._/+~-]+"
)
_GENERIC_PART_TERMS = (
    "火花塞",
    "机油",
    "机滤",
    "空滤",
    "空调滤",
    "刹车片",
    "刹车盘",
    "轮胎",
    "电瓶",
    "蓄电池",
)
_CONFIRMATIONS = {"确认", "确定", "可以", "没问题", "是的", "好的", "下单"}


def _context(runtime: ToolRuntime) -> tuple[str, str, tuple[str, ...]]:
    context = getattr(runtime, "context", None)
    user_id = require_user_id(context)
    username = getattr(context, "username", None) or user_id
    roles = tuple(getattr(context, "roles", ()) or ())
    return user_id, username, roles


def _conversation_id(runtime: ToolRuntime) -> str | None:
    """当前对话 ID；只从服务端运行时上下文读取，缺失时由调用方失败关闭。"""
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        value = context.get("thread_id")
    else:
        value = getattr(context, "thread_id", None)
    normalized = str(value or "").strip()
    return normalized or None


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
        "conversation_id": data.get("conversation_id"),
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
            "warehouse_location、budget、required_by、preferred_supplier_id、"
            "preferred_supplier_name、priority。"
            "用户已经确认物料型号时，items[].part_name 必须保留具体型号，"
            "禁止只传“火花塞”等品类词。"
            "不要使用 material_id/material_name/part 等别名；"
            "不要在模型侧编造 part_id/预算/仓库。"
        ),
    )


def _normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    """兼容模型常见的字段别名，最终统一为 ProcurementRequest 契约。"""
    return normalize_procurement_request(request)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            if isinstance(item, dict)
            else str(item)
            for item in content
        )
    return str(content or "")


def _part_models(text: str) -> list[str]:
    models = []
    seen = set()
    for match in _PART_MODEL_TOKEN.findall(text):
        key = match.casefold()
        if key not in seen:
            seen.add(key)
            models.append(match)
    return models


def _is_confirmation(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?]", "", text)
    return normalized in _CONFIRMATIONS


def _is_generic_part_name(name: str) -> bool:
    return bool(name) and not _part_models(name) and any(
        term in name for term in _GENERIC_PART_TERMS
    )


def _restore_confirmed_part_identity(
    request: dict[str, Any],
    messages: list[Any] | None,
) -> dict[str, Any]:
    """补回最近一轮用户已明确并确认的唯一型号，不跨历史猜物料。"""
    data = deepcopy(request)
    items = data.get("items")
    if not isinstance(items, list) or len(items) != 1:
        return data
    item = items[0]
    if not isinstance(item, dict):
        return data
    if item.get("part_id") is not None or item.get("part_code"):
        return data
    part_name = str(item.get("part_name") or "").strip()
    if not _is_generic_part_name(part_name):
        return data

    human_texts = [
        _message_text(getattr(message, "content", ""))
        for message in messages or []
        if getattr(message, "type", None) == "human"
    ]
    if not human_texts:
        return data

    latest_models = _part_models(human_texts[-1])
    if not latest_models and len(human_texts) >= 2 and _is_confirmation(
        human_texts[-1]
    ):
        latest_models = _part_models(human_texts[-2])

    unique_models = {
        model.casefold(): model for model in latest_models
    }
    if len(unique_models) != 1:
        return data
    model = next(iter(unique_models.values()))
    if model.casefold() not in part_name.casefold():
        item["part_name"] = f"{part_name} {model}"
    return data


def _runtime_messages(runtime: ToolRuntime) -> list[Any]:
    state = getattr(runtime, "state", None)
    if not isinstance(state, dict):
        return []
    messages = state.get("messages")
    return messages if isinstance(messages, list) else []


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
    conversation_id = _conversation_id(runtime)
    if not conversation_id:
        return json.dumps(
            {
                "status": "error",
                "error": "CONVERSATION_CONTEXT_MISSING",
                "message": "缺少来源对话，任务未创建；请从对话中重新发起。",
            },
            ensure_ascii=False,
        )
    runner = await get_task_runner()
    normalized = _normalize_request(request)
    normalized = _restore_confirmed_part_identity(
        normalized,
        _runtime_messages(runtime),
    )
    try:
        result = await runner.start_background(
            raw_request="",
            user_id=user_id,
            username=username,
            conversation_id=conversation_id,
            request=normalized,
            roles=roles,
        )
        # 后台任务会通过 SSE 继续推进；前端收到 task_id 后打开采购任务面板。
        return _dump(_public_result(result))
    except ProcurementRequestInputError as exc:
        return json.dumps(
            {
                "status": "error",
                "error": exc.code,
                "message": str(exc),
                "field_errors": exc.errors,
            },
            ensure_ascii=False,
        )
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
                "返回 task_id；审批、补充和重试由用户在任务面板完成。"
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
