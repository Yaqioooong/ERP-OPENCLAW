"""显式偏好保存工具。"""
from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent.memory.identity import (
    MemoryIdentityError,
    require_user_id,
)
from agent.memory.preference_service import (
    EXPLICIT_FIELDS,
    PreferenceMemoryService,
)


class SaveUserPreferencesInput(BaseModel):
    updates: dict[str, Any] = Field(
        ...,
        description=(
            "只包含要修改的显式偏好字段，可选键："
            "preferred_output、preferred_chart_type、preferred_currency、"
            "preferred_language。未提供的字段保持不变；传 null 表示清除。"
            "不要传 recent_suppliers / recent_queries。"
        ),
    )


def build_preference_tools(service: PreferenceMemoryService) -> list[StructuredTool]:
    async def save_user_preferences(
        updates: dict[str, Any],
        runtime: ToolRuntime,
    ) -> str:
        """保存用户明确要求记住的偏好，并返回可判定的保存结果。"""
        context = getattr(runtime, "context", None)
        try:
            user_id = require_user_id(context)
        except MemoryIdentityError:
            return json.dumps(
                {
                    "saved": False,
                    "reason": "permission_denied",
                    "retryable": False,
                    "message": "缺少可信用户身份，偏好未保存",
                },
                ensure_ascii=False,
            )

        unknown = set(updates or {}) - set(EXPLICIT_FIELDS)
        if unknown or not updates:
            return json.dumps(
                {
                    "saved": False,
                    "reason": "invalid_request",
                    "retryable": False,
                    "message": "偏好参数不合法，本次未保存",
                },
                ensure_ascii=False,
            )

        try:
            result = await service.save_explicit(
                user_id,
                updates,
                request_id=str(getattr(context, "request_id", "") or ""),
                thread_id=str(getattr(context, "thread_id", "") or ""),
                source_message_id=str(
                    getattr(runtime, "tool_call_id", None)
                    or getattr(context, "request_id", "")
                    or "explicit-tool"
                ),
            )
        except Exception:
            return json.dumps(
                {
                    "saved": False,
                    "reason": "write_failed",
                    "retryable": True,
                    "message": "偏好未保存，请稍后重试",
                },
                ensure_ascii=False,
            )
        if result.ok:
            return json.dumps(
                {
                    "saved": True,
                    "revision": result.revision,
                    "changed": result.changed,
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "saved": False,
                "reason": result.reason,
                "retryable": result.retryable,
                "message": result.message or "偏好未保存",
            },
            ensure_ascii=False,
        )

    return [
        StructuredTool.from_function(
            coroutine=save_user_preferences,
            name="save_user_preferences",
            description=(
                "当用户明确要求记住/修改长期偏好时调用。只接受 preferred_output、"
                "preferred_chart_type、preferred_currency、preferred_language；"
                "不要用它保存近期供应商/查询。必须依据返回的 saved 字段告诉用户"
                "“已保存”或“未保存”，禁止在 saved=false 时声称已保存。"
            ),
            args_schema=SaveUserPreferencesInput,
        )
    ]
