"""用户长期记忆查看/删除/清空接口。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agent.config import REDIS_CLIENT, STORE
from agent.memory.audit import log_memory_event
from agent.memory.identity import preferences_key
from agent.memory.metrics import metrics
from agent.memory.preference_lock import RedisPreferenceLock
from agent.memory.preference_service import (
    PreferenceMemoryService,
)
from agent.memory.preferences import (
    MAX_RECENT_QUERIES,
    MAX_RECENT_SUPPLIERS,
    RECENT_QUERY_TTL_DAYS,
    RECENT_SUPPLIER_TTL_DAYS,
    PreferenceSchemaError,
)
from agent.memory.user_memory_backend import UserMemoryBackend
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready, require_roles

router = APIRouter()


class DeleteMemoryItemRequest(BaseModel):
    memory_type: Literal["recent_suppliers", "recent_queries"]
    value: str = Field(..., min_length=1, max_length=500)


def _build_service(user_id: str) -> PreferenceMemoryService:
    backend = UserMemoryBackend(
        store=STORE,
        namespace=lambda _rt: (user_id,),
    )
    return PreferenceMemoryService(
        backend=backend,
        lock=RedisPreferenceLock(REDIS_CLIENT),
    )


async def get_memory_service(
    user: AuthenticatedUser = Depends(require_password_ready),
) -> PreferenceMemoryService:
    return _build_service(user.user_id)


def _item_view(item, ttl_days: int) -> dict:
    return {
        "value": item.value,
        "created_at": item.created_at.isoformat(),
        "last_seen_at": item.last_seen_at.isoformat(),
        "expires_at": (item.last_seen_at + timedelta(days=ttl_days)).isoformat(),
    }


def _preferences_view(preferences) -> dict:
    return {
        "schema_version": preferences.schema_version,
        "revision": preferences.revision,
        "preferred": {
            "preferred_output": preferences.preferred_output,
            "preferred_chart_type": preferences.preferred_chart_type,
            "preferred_currency": preferences.preferred_currency,
            "preferred_language": preferences.preferred_language,
        },
        "recent_suppliers": [
            _item_view(item, RECENT_SUPPLIER_TTL_DAYS)
            for item in preferences.recent_suppliers
        ],
        "recent_queries": [
            _item_view(item, RECENT_QUERY_TTL_DAYS)
            for item in preferences.recent_queries
        ],
        "limits": {
            "supplier_ttl_days": RECENT_SUPPLIER_TTL_DAYS,
            "query_ttl_days": RECENT_QUERY_TTL_DAYS,
            "max_suppliers": MAX_RECENT_SUPPLIERS,
            "max_queries": MAX_RECENT_QUERIES,
        },
    }


@router.get("/memory/metrics")
async def memory_metrics(
    _admin=Depends(require_roles("admin")),
):
    """进程内最小记忆指标；生产环境可替换为 Prometheus 导出。"""
    return metrics.snapshot()


@router.get("/memory/preferences")
async def get_preferences(
    user: AuthenticatedUser = Depends(require_password_ready),
    service: PreferenceMemoryService = Depends(get_memory_service),
):
    try:
        preferences = await service.get_preferences(user.user_id)
    except PreferenceSchemaError as exc:
        raise HTTPException(status_code=409, detail=f"偏好文件不可读: {exc.reason}") from exc
    return _preferences_view(preferences)


@router.delete("/memory/preferences/items")
async def delete_memory_item(
    request: DeleteMemoryItemRequest,
    user: AuthenticatedUser = Depends(require_password_ready),
    service: PreferenceMemoryService = Depends(get_memory_service),
):
    request_id = str(uuid.uuid4())
    try:
        result = await service.delete_recent_item(
            user.user_id,
            request.memory_type,
            request.value,
            request_id=request_id,
        )
    except PreferenceSchemaError as exc:
        raise HTTPException(status_code=409, detail=f"偏好文件不可读: {exc.reason}") from exc

    if not result.ok:
        if result.reason == "not_found":
            raise HTTPException(status_code=404, detail="记忆不存在")
        raise HTTPException(status_code=503, detail="记忆删除暂不可用，请稍后重试")

    log_memory_event(
        op="memory.delete_item",
        user_id=user.user_id,
        namespace=(user.user_id,),
        path=preferences_key(user.user_id),
        request_id=request_id,
        outcome="ok",
        reason=request.memory_type,
        revision=result.revision,
    )
    return {"deleted": True, "revision": result.revision}


@router.delete("/memory/preferences")
async def clear_preferences(
    user: AuthenticatedUser = Depends(require_password_ready),
    service: PreferenceMemoryService = Depends(get_memory_service),
):
    request_id = str(uuid.uuid4())
    try:
        result = await service.clear_all(user.user_id, request_id=request_id)
    except PreferenceSchemaError as exc:
        raise HTTPException(status_code=409, detail=f"偏好文件不可读: {exc.reason}") from exc

    if not result.ok:
        raise HTTPException(status_code=503, detail="记忆清空暂不可用，请稍后重试")

    log_memory_event(
        op="memory.clear",
        user_id=user.user_id,
        namespace=(user.user_id,),
        path=preferences_key(user.user_id),
        request_id=request_id,
        outcome="ok",
        revision=result.revision,
    )
    return {"cleared": True, "revision": result.revision}
