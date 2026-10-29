"""管理员用户管理与角色申请审批接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api_view.auth.audit import log_auth_event
from api_view.auth.models import AuthenticatedUser
from api_view.auth.passwords import hash_password
from api_view.auth.permissions import require_roles
from api_view.auth.sessions import SessionStore, get_session_store
from api_view.auth.users import (
    LastAdminError,
    RoleRequestAlreadyReviewed,
    RoleRequestNotFound,
    SelfApprovalError,
    UserNotFound,
    UserRepository,
    get_user_repository,
    normalize_roles,
)
from api_view.auth.validation import (
    AuthValidationError,
    generate_temporary_password,
    validate_display_name,
)

router = APIRouter()


class AdminUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    roles: list[str] | None = None


class ReviewRequest(BaseModel):
    comment: str = Field("", max_length=500)


def _public_user(record) -> dict:
    return record.public_dict()


def _role_request_dict(record) -> dict:
    return {
        "request_id": record.request_id,
        "user_id": record.user_id,
        "username": record.username,
        "display_name": record.display_name,
        "requested_role": record.requested_role,
        "reason": record.reason,
        "status": record.status,
        "reviewed_by": record.reviewed_by,
        "review_comment": record.review_comment,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
    }


@router.get("/admin/users")
async def list_users(
    query: str | None = Query(default=None, max_length=128),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
):
    records, total = await users.list_users(
        query=query, is_active=is_active, limit=limit, offset=offset
    )
    return {
        "items": [_public_user(record) for record in records],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/admin/users/{user_id}")
async def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
):
    target = await users.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    if target.user_id == admin.user_id and (
        payload.roles is not None or payload.is_active is not None
    ):
        raise HTTPException(status_code=409, detail="不能修改自己的角色或状态")

    roles = None
    if payload.roles is not None:
        try:
            roles = normalize_roles(payload.roles)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not roles:
            raise HTTPException(status_code=422, detail="用户至少需要一个角色")

    target_is_active_admin = target.is_active and "admin" in target.roles
    removes_admin = roles is not None and "admin" not in roles
    disables = payload.is_active is False
    if target_is_active_admin and (removes_admin or disables):
        if await users.count_active_admins() <= 1:
            raise HTTPException(status_code=409, detail="不能移除最后一个有效管理员")

    try:
        if payload.display_name is not None:
            try:
                display_name = validate_display_name(payload.display_name)
            except AuthValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "validation_error", "field_errors": exc.field_errors},
                ) from exc
            await users.set_display_name(target.user_id, display_name)
        if roles is not None:
            await users.set_roles(target.user_id, roles)
        if payload.is_active is not None:
            await users.set_active(target.user_id, payload.is_active)
    except UserNotFound as exc:
        raise HTTPException(status_code=404, detail="用户不存在") from exc
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if payload.is_active is False:
        sessions.revoke_all(target.user_id)

    updated = await users.get(target.user_id)
    assert updated is not None
    log_auth_event(
        event="admin_user_update",
        actor=admin.user_id,
        target=target.user_id,
        outcome="ok",
        detail="roles/status/display_name",
    )
    return {"user": _public_user(updated)}


@router.post("/admin/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
):
    target = await users.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    temporary_password = generate_temporary_password()
    await users.update_password(
        target.user_id,
        hash_password(temporary_password),
        must_change_password=True,
    )
    sessions.revoke_all(target.user_id)
    log_auth_event(
        event="admin_password_reset",
        actor=admin.user_id,
        target=target.user_id,
        outcome="ok",
    )
    return {
        "user_id": target.user_id,
        "temporary_password": temporary_password,
        "must_change_password": True,
    }


@router.get("/admin/role-requests")
async def list_role_requests(
    status_filter: str = Query(default="pending", alias="status"),
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
):
    if status_filter not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="非法状态")
    records = await users.list_role_requests(status=status_filter)
    return {"requests": [_role_request_dict(record) for record in records]}


@router.post("/admin/role-requests/{request_id}/approve")
async def approve_role_request(
    request_id: str,
    payload: ReviewRequest,
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
):
    try:
        record = await users.approve_role_request(
            request_id, reviewer_id=admin.user_id, comment=payload.comment
        )
    except RoleRequestNotFound as exc:
        raise HTTPException(status_code=404, detail="申请不存在") from exc
    except RoleRequestAlreadyReviewed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    log_auth_event(
        event="role_request_approved",
        actor=admin.user_id,
        target=record.user_id,
        outcome="ok",
    )
    return {"request": _role_request_dict(record)}


@router.post("/admin/role-requests/{request_id}/reject")
async def reject_role_request(
    request_id: str,
    payload: ReviewRequest,
    admin: AuthenticatedUser = Depends(require_roles("admin")),
    users: UserRepository = Depends(get_user_repository),
):
    try:
        record = await users.reject_role_request(
            request_id, reviewer_id=admin.user_id, comment=payload.comment
        )
    except RoleRequestNotFound as exc:
        raise HTTPException(status_code=404, detail="申请不存在") from exc
    except RoleRequestAlreadyReviewed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    log_auth_event(
        event="role_request_rejected",
        actor=admin.user_id,
        target=record.user_id,
        outcome="ok",
    )
    return {"request": _role_request_dict(record)}
