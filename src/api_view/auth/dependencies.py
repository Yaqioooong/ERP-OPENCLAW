"""FastAPI 认证依赖。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from agent.memory.identity import MemoryIdentityError, validate_user_id
from api_view.auth import config
from api_view.auth.models import AuthenticatedUser
from api_view.auth.sessions import SessionStore, get_session_store
from api_view.auth.users import UserRepository, get_user_repository


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(config.COOKIE_NAME)
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def get_client_ip(request: Request) -> str:
    client = request.client
    return client.host if client and client.host else "unknown"


def require_trusted_origin(request: Request) -> None:
    """对带 Origin 的写请求做白名单校验（SameSite=Lax 之外的第二道）。"""
    if not config.origin_allowed(request.headers.get("origin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="来源不被允许",
        )


def _unauthorized(detail: str = "未认证或会话已失效") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Cookie"},
    )


async def get_current_user(
    request: Request,
    sessions: SessionStore = Depends(get_session_store),
    users: UserRepository = Depends(get_user_repository),
) -> AuthenticatedUser:
    """解析可信会话并回查数据库，确保禁用/角色变更立即生效。"""
    token = _token_from_request(request)
    if not token:
        raise _unauthorized()
    session_user = sessions.resolve(token)
    if session_user is None:
        raise _unauthorized()
    try:
        user_id = validate_user_id(session_user.user_id)
    except MemoryIdentityError as exc:
        sessions.revoke(token)
        raise _unauthorized() from exc

    record = await users.get(user_id)
    if record is None or not record.is_active:
        # 账号被删除/禁用后，旧会话立即失效。
        sessions.revoke(token)
        raise _unauthorized()

    return AuthenticatedUser(
        user_id=record.user_id,
        username=record.username,
        roles=record.roles,
        display_name=record.display_name or record.username,
        must_change_password=record.must_change_password,
    )
