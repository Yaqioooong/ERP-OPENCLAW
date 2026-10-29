"""接口级权限依赖。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from api_view.auth.dependencies import get_current_user
from api_view.auth.models import AuthenticatedUser


async def require_password_ready(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """管理员重置密码后，必须先改密才能访问业务接口。"""
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "password_change_required",
                "message": "请先修改密码后再继续操作",
            },
        )
    return user


def require_roles(*allowed_roles: str):
    """返回一个 FastAPI 依赖：要求用户至少拥有一个指定角色。"""
    allowed = set(allowed_roles)

    async def dependency(
        user: AuthenticatedUser = Depends(require_password_ready),
    ) -> AuthenticatedUser:
        if not (allowed & user.role_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="没有执行该操作的权限",
            )
        return user

    return dependency
