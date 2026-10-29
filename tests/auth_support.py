"""认证 API 测试用的内存用户仓储与会话装配。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from fastapi import FastAPI

from api_view.auth import config as auth_config
from api_view.auth.admin_router import router as admin_router
from api_view.auth.models import AuthenticatedUser
from api_view.auth.passwords import hash_password
from api_view.auth.router import router as auth_router
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
    RoleRequestAlreadyReviewed,
    RoleRequestNotFound,
    SelfApprovalError,
    UserAlreadyExists,
    UserNotFound,
    UserRecord,
    get_user_repository,
)
from tests.fakes import FakeRedis


def make_record(
    *,
    user_id: str = "u_1001",
    username: str = "zhangsan",
    password: str = "password-123",
    roles: tuple[str, ...] = ("requester",),
    active: bool = True,
    display_name: str = "",
    must_change_password: bool = False,
) -> UserRecord:
    now = datetime.now(timezone.utc)
    return UserRecord(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        roles=roles,
        is_active=active,
        display_name=display_name or username,
        must_change_password=must_change_password,
        created_at=now,
        updated_at=now,
    )


class InMemoryUsers:
    def __init__(self, records=None):
        self.by_id: dict[str, UserRecord] = {}
        self.requests: dict[str, dict] = {}
        for record in records or []:
            self.by_id[record.user_id] = record

    async def get_by_username(self, username: str):
        for record in self.by_id.values():
            if record.username.lower() == username.lower():
                return record
        return None

    async def get(self, user_id: str):
        return self.by_id.get(user_id)

    async def create(self, **kwargs):
        if any(r.username.lower() == kwargs["username"].lower() for r in self.by_id.values()):
            raise UserAlreadyExists("用户名已存在")
        now = datetime.now(timezone.utc)
        record = UserRecord(
            user_id=kwargs["user_id"],
            username=kwargs["username"],
            password_hash=kwargs["password_hash"],
            roles=tuple(kwargs.get("roles", ("requester",))),
            is_active=kwargs.get("is_active", True),
            display_name=kwargs.get("display_name", ""),
            must_change_password=kwargs.get("must_change_password", False),
            created_at=now,
            updated_at=now,
            password_changed_at=now,
        )
        self.by_id[record.user_id] = record
        return record

    async def mark_login(self, user_id: str):
        record = self.by_id.get(user_id)
        if record:
            self.by_id[user_id] = replace(record, last_login_at=datetime.now(timezone.utc))

    async def update_password(self, user_id: str, password_hash: str, *, must_change_password: bool = False):
        record = self.by_id.get(user_id)
        if record is None:
            raise UserNotFound(user_id)
        self.by_id[user_id] = replace(
            record,
            password_hash=password_hash,
            must_change_password=must_change_password,
            password_changed_at=datetime.now(timezone.utc),
        )

    async def set_active(self, user_id: str, is_active: bool):
        record = self.by_id.get(user_id)
        if record is None:
            raise UserNotFound(user_id)
        self.by_id[user_id] = replace(record, is_active=is_active)

    async def set_roles(self, user_id: str, roles):
        record = self.by_id.get(user_id)
        if record is None:
            raise UserNotFound(user_id)
        self.by_id[user_id] = replace(record, roles=tuple(roles))

    async def set_display_name(self, user_id: str, display_name: str):
        record = self.by_id.get(user_id)
        if record is None:
            raise UserNotFound(user_id)
        self.by_id[user_id] = replace(record, display_name=display_name)

    async def list_users(self, *, query=None, is_active=None, limit=50, offset=0):
        records = list(self.by_id.values())
        if query:
            q = query.lower()
            records = [
                r for r in records
                if q in r.username.lower() or q in r.display_name.lower() or q in r.user_id.lower()
            ]
        if is_active is not None:
            records = [r for r in records if r.is_active == is_active]
        records.sort(key=lambda r: r.user_id)
        return records[offset:offset + limit], len(records)

    async def count(self):
        return len(self.by_id)

    async def count_users(self):
        return len(self.by_id)

    async def count_active_admins(self):
        return sum(1 for r in self.by_id.values() if r.is_active and "admin" in r.roles)

    async def create_role_request(self, *, request_id, user_id, requested_role, reason):
        if requested_role != "approver":
            raise ValueError("只允许申请 approver")
        if any(
            r["user_id"] == user_id and r["status"] == "pending"
            for r in self.requests.values()
        ):
            raise PendingRoleRequest("已有待审批的同类申请")
        record = self.by_id.get(user_id)
        if record is None:
            raise UserNotFound(user_id)
        from api_view.auth.users import RoleRequestRecord

        data = {
            "request_id": request_id,
            "user_id": user_id,
            "username": record.username,
            "display_name": record.display_name,
            "requested_role": requested_role,
            "reason": reason,
            "status": "pending",
            "reviewed_by": None,
            "review_comment": "",
            "created_at": datetime.now(timezone.utc),
            "reviewed_at": None,
        }
        self.requests[request_id] = data
        return RoleRequestRecord(**data)

    async def get_role_request(self, request_id):
        from api_view.auth.users import RoleRequestRecord

        data = self.requests.get(request_id)
        return RoleRequestRecord(**data) if data else None

    async def list_role_requests(self, *, status=None, user_id=None):
        from api_view.auth.users import RoleRequestRecord

        rows = list(self.requests.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        if user_id:
            rows = [r for r in rows if r["user_id"] == user_id]
        return [RoleRequestRecord(**row) for row in rows]

    async def approve_role_request(self, request_id, *, reviewer_id, comment=""):
        data = self.requests.get(request_id)
        if data is None:
            raise RoleRequestNotFound(request_id)
        if data["status"] != "pending":
            raise RoleRequestAlreadyReviewed("申请已处理")
        if data["user_id"] == reviewer_id:
            raise SelfApprovalError("不能审批自己的申请")
        data.update(
            status="approved",
            reviewed_by=reviewer_id,
            review_comment=comment,
            reviewed_at=datetime.now(timezone.utc),
        )
        record = self.by_id[data["user_id"]]
        roles = tuple(dict.fromkeys(record.roles + (data["requested_role"],)))
        self.by_id[record.user_id] = replace(record, roles=roles)
        return await self.get_role_request(request_id)

    async def reject_role_request(self, request_id, *, reviewer_id, comment=""):
        data = self.requests.get(request_id)
        if data is None:
            raise RoleRequestNotFound(request_id)
        if data["status"] != "pending":
            raise RoleRequestAlreadyReviewed("申请已处理")
        if data["user_id"] == reviewer_id:
            raise SelfApprovalError("不能审批自己的申请")
        data.update(
            status="rejected",
            reviewed_by=reviewer_id,
            review_comment=comment,
            reviewed_at=datetime.now(timezone.utc),
        )
        return await self.get_role_request(request_id)


def build_auth_app(users: InMemoryUsers):
    redis = FakeRedis()
    sessions = SessionStore(redis, ttl_seconds=600)
    login_limiter = LoginRateLimiter(redis, max_failures=10, window_seconds=60)
    register_limiter = RegistrationLimiter(redis, max_attempts=100, window_seconds=3600)

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.dependency_overrides[get_user_repository] = lambda: users
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_login_limiter] = lambda: login_limiter
    app.dependency_overrides[get_registration_limiter] = lambda: register_limiter
    return app, sessions, login_limiter, register_limiter


def login(client, username="zhangsan", password="password-123"):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
