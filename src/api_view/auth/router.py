"""登录、注册、登出、改密与角色申请接口。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from api_view.auth import config
from api_view.auth.audit import log_auth_event
from api_view.auth.dependencies import (
    get_client_ip,
    get_current_user,
    require_trusted_origin,
)
from api_view.auth.models import AuthenticatedUser
from api_view.auth.passwords import dummy_password_hash, hash_password, verify_password
from api_view.auth.permissions import require_password_ready
from api_view.auth.sessions import (
    LoginRateLimiter,
    RegistrationLimiter,
    SessionStore,
    get_login_limiter,
    get_registration_limiter,
    get_session_store,
)
from api_view.auth.users import (
    PendingRoleRequest,
    UserAlreadyExists,
    UserRepository,
    get_user_repository,
)
from api_view.auth.validation import (
    AuthValidationError,
    generate_user_id,
    validate_password,
    validate_password_confirmation,
    validate_registration,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    username: str = Field(..., max_length=128)
    display_name: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)
    confirm_password: str = Field(..., max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=128)
    confirm_password: str = Field(..., min_length=1, max_length=128)


class RoleRequestCreate(BaseModel):
    role: str = Field(..., max_length=32)
    reason: str = Field("", max_length=500)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=config.COOKIE_NAME,
        path="/",
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
    )


def _validation_http_error(exc: AuthValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "validation_error", "field_errors": exc.field_errors},
    )


@router.get("/auth/policy")
async def auth_policy():
    """公开的注册/密码规则，供前端展示与预校验。"""
    return {
        "registration_enabled": True,
        "username": {
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$",
            "min": 3,
            "max": 32,
            "description": "字母或数字开头，仅允许字母、数字、点、下划线、短横线",
        },
        "password": {
            "min": 8,
            "max": 128,
            "description": "至少 8 位，同时包含字母和数字，不能包含用户名",
        },
        "roles": ["requester", "approver", "admin"],
        "role_requests": ["approver"],
    }


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
    limiter: RegistrationLimiter = Depends(get_registration_limiter),
):
    require_trusted_origin(request)
    client_ip = get_client_ip(request)
    if limiter.is_blocked(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="注册过于频繁，请稍后再试",
        )
    limiter.record(client_ip)

    try:
        username, display_name, password = validate_registration(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            confirm_password=payload.confirm_password,
        )
    except AuthValidationError as exc:
        raise _validation_http_error(exc) from exc

    if await users.get_by_username(username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "username_taken", "message": "用户名已被占用"},
        )

    record = None
    for _ in range(3):
        try:
            record = await users.create(
                user_id=generate_user_id(),
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                roles=("requester",),
            )
            break
        except UserAlreadyExists:
            # 可能是 user_id 碰撞，也可能是并发注册同名；重新检查用户名。
            if await users.get_by_username(username) is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "username_taken", "message": "用户名已被占用"},
                ) from None
    if record is None:
        raise HTTPException(status_code=503, detail="注册失败，请稍后重试")

    token = sessions.create(
        AuthenticatedUser(
            user_id=record.user_id,
            username=record.username,
            roles=record.roles,
            display_name=record.display_name or record.username,
            must_change_password=False,
        )
    )
    _set_session_cookie(response, token)
    response.headers["Cache-Control"] = "no-store"
    log_auth_event(
        event="register",
        actor=record.user_id,
        target=record.user_id,
        client_ip=client_ip,
        outcome="ok",
    )
    return {"user": record.public_dict()}


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
    limiter: LoginRateLimiter = Depends(get_login_limiter),
):
    require_trusted_origin(request)
    username = payload.username.strip()
    client_ip = get_client_ip(request)

    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if limiter.is_blocked(username, client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录失败次数过多，请稍后再试",
        )

    record = await users.get_by_username(username)
    password_hash = record.password_hash if record is not None else dummy_password_hash()
    valid = verify_password(payload.password, password_hash)
    if record is None or not valid or not record.is_active:
        limiter.record_failure(username, client_ip)
        log_auth_event(
            event="login",
            target=username,
            client_ip=client_ip,
            outcome="failed",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    limiter.reset(username, client_ip)
    await users.mark_login(record.user_id)
    user = AuthenticatedUser(
        user_id=record.user_id,
        username=record.username,
        roles=record.roles,
        display_name=record.display_name or record.username,
        must_change_password=record.must_change_password,
    )
    token = sessions.create(user)
    _set_session_cookie(response, token)
    response.headers["Cache-Control"] = "no-store"
    log_auth_event(
        event="login",
        actor=record.user_id,
        target=record.user_id,
        client_ip=client_ip,
        outcome="ok",
    )
    return {
        "user": user.as_public_dict(),
        "must_change_password": user.must_change_password,
    }


@router.post("/auth/dev-login")
async def dev_login(
    request: Request,
    response: Response,
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
):
    """仅开发模式且显式配置演示用户时可用；生产环境不存在该入口。"""
    if not config.dev_demo_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    require_trusted_origin(request)

    record = await users.get(config.DEV_DEMO_USER_ID)
    if record is None:
        record = await users.create(
            user_id=config.DEV_DEMO_USER_ID,
            username=config.DEV_DEMO_USERNAME or config.DEV_DEMO_USER_ID,
            display_name="Demo User",
            password_hash=hash_password(config.DEV_DEMO_USER_ID + "-dev-only"),
            roles=config.DEV_DEMO_ROLES or ("requester",),
        )
    if not record.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")

    user = AuthenticatedUser(
        user_id=record.user_id,
        username=record.username,
        roles=record.roles,
        display_name=record.display_name or record.username,
        must_change_password=record.must_change_password,
    )
    token = sessions.create(user)
    _set_session_cookie(response, token)
    log_auth_event(
        event="dev_login",
        actor=record.user_id,
        target=record.user_id,
        client_ip=get_client_ip(request),
        outcome="ok",
    )
    return {"user": user.as_public_dict(), "must_change_password": user.must_change_password}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    sessions: SessionStore = Depends(get_session_store),
):
    require_trusted_origin(request)
    token = request.cookies.get(config.COOKIE_NAME)
    sessions.revoke(token)
    _clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me")
async def me(user: AuthenticatedUser = Depends(get_current_user)):
    return {"user": user.as_public_dict()}


@router.post("/auth/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    users: UserRepository = Depends(get_user_repository),
    sessions: SessionStore = Depends(get_session_store),
):
    record = await users.get(user.user_id)
    if record is None or not verify_password(payload.current_password, record.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    try:
        validate_password(payload.new_password, record.username)
        validate_password_confirmation(payload.new_password, payload.confirm_password)
    except AuthValidationError as exc:
        raise _validation_http_error(exc) from exc
    if verify_password(payload.new_password, record.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    await users.update_password(
        record.user_id,
        hash_password(payload.new_password),
        must_change_password=False,
    )
    sessions.revoke_all(record.user_id)
    refreshed = await users.get(record.user_id)
    assert refreshed is not None
    new_user = AuthenticatedUser(
        user_id=refreshed.user_id,
        username=refreshed.username,
        roles=refreshed.roles,
        display_name=refreshed.display_name or refreshed.username,
        must_change_password=refreshed.must_change_password,
    )
    token = sessions.create(new_user)
    _set_session_cookie(response, token)
    log_auth_event(
        event="password_change",
        actor=user.user_id,
        target=user.user_id,
        client_ip=get_client_ip(request),
        outcome="ok",
    )
    return {"user": new_user.as_public_dict()}


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    sessions: SessionStore = Depends(get_session_store),
):
    sessions.revoke_all(user.user_id)
    _clear_session_cookie(response)
    log_auth_event(
        event="logout_all",
        actor=user.user_id,
        target=user.user_id,
        client_ip=get_client_ip(request),
        outcome="ok",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/auth/role-requests", status_code=status.HTTP_201_CREATED)
async def create_role_request(
    payload: RoleRequestCreate,
    user: AuthenticatedUser = Depends(require_password_ready),
    users: UserRepository = Depends(get_user_repository),
):
    if payload.role != "approver":
        raise HTTPException(
            status_code=422,
            detail="当前只支持申请 approver 角色",
        )
    if user.role_set & {"approver", "admin"}:
        raise HTTPException(status_code=409, detail="当前角色无需申请")
    try:
        record = await users.create_role_request(
            request_id=f"rr_{uuid.uuid4().hex[:12]}",
            user_id=user.user_id,
            requested_role=payload.role,
            reason=payload.reason,
        )
    except PendingRoleRequest as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_auth_event(
        event="role_request",
        actor=user.user_id,
        target=record.request_id,
        outcome="ok",
    )
    return {"request": _role_request_dict(record)}


@router.get("/auth/role-requests/mine")
async def my_role_requests(
    user: AuthenticatedUser = Depends(require_password_ready),
    users: UserRepository = Depends(get_user_repository),
):
    records = await users.list_role_requests(user_id=user.user_id)
    return {"requests": [_role_request_dict(record) for record in records]}


def _role_request_dict(record) -> dict:
    return {
        "request_id": record.request_id,
        "user_id": record.user_id,
        "requested_role": record.requested_role,
        "reason": record.reason,
        "status": record.status,
        "review_comment": record.review_comment,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
    }
