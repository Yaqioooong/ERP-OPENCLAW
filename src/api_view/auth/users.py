"""认证用户与角色申请仓储。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import asyncpg

from agent.memory.identity import validate_user_id
from agent.procurement.database import Database
from api_view.auth import config

ALLOWED_ROLES = ("requester", "approver", "admin")


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    roles: tuple[str, ...]
    is_active: bool
    display_name: str = ""
    must_change_password: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    password_changed_at: datetime | None = None

    def public_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "roles": list(self.roles),
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RoleRequestRecord:
    request_id: str
    user_id: str
    username: str
    display_name: str
    requested_role: str
    reason: str
    status: str
    reviewed_by: str | None
    review_comment: str
    created_at: datetime
    reviewed_at: datetime | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_user (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    roles         TEXT[] NOT NULL DEFAULT '{}',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    password_changed_at TIMESTAMPTZ
);

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_user_username_lower
    ON app_user (lower(username));

CREATE TABLE IF NOT EXISTS app_role_request (
    request_id     TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    requested_role TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    reviewed_by    TEXT REFERENCES app_user(user_id),
    review_comment TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL,
    reviewed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_role_request_status
    ON app_role_request(status, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_role_request_one_pending
    ON app_role_request(user_id, requested_role)
    WHERE status = 'pending';
"""


class UserAlreadyExists(ValueError):
    pass


class UserNotFound(KeyError):
    pass


class LastAdminError(ValueError):
    """不能禁用或降级最后一个有效管理员。"""


class SelfModificationError(ValueError):
    """管理员不能修改自己的角色/状态。"""


class PendingRoleRequest(ValueError):
    pass


class RoleRequestNotFound(KeyError):
    pass


class RoleRequestAlreadyReviewed(ValueError):
    pass


class SelfApprovalError(ValueError):
    pass


def _normalize_username(username: str) -> str:
    value = (username or "").strip()
    if not value or len(value) > 128:
        raise ValueError("用户名不能为空且不能超过 128 个字符")
    return value


def normalize_roles(roles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(
        dict.fromkeys(role.strip() for role in roles if role and role.strip())
    )
    invalid = [role for role in normalized if role not in ALLOWED_ROLES]
    if invalid:
        raise ValueError(f"非法角色: {', '.join(invalid)}")
    return normalized


class UserRepository:
    def __init__(self, database: Database):
        self._database = database

    async def setup(self) -> None:
        async with self._database.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def get_by_username(self, username: str) -> UserRecord | None:
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, username, display_name, password_hash, roles,
                       is_active, must_change_password, last_login_at,
                       created_at, updated_at, password_changed_at
                FROM app_user
                WHERE lower(username) = lower($1)
                """,
                _normalize_username(username),
            )
        return _row_to_record(row)

    async def get(self, user_id: str) -> UserRecord | None:
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, username, display_name, password_hash, roles,
                       is_active, must_change_password, last_login_at,
                       created_at, updated_at, password_changed_at
                FROM app_user
                WHERE user_id = $1
                """,
                validate_user_id(user_id),
            )
        return _row_to_record(row)

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        roles: Iterable[str] = ("requester",),
        display_name: str = "",
        is_active: bool = True,
        must_change_password: bool = False,
    ) -> UserRecord:
        user_id = validate_user_id(user_id)
        username = _normalize_username(username)
        if not password_hash:
            raise ValueError("password_hash 不能为空")
        now = datetime.now(timezone.utc)
        try:
            async with self._database.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_user
                        (user_id, username, display_name, password_hash, roles,
                         is_active, must_change_password, created_at, updated_at,
                         password_changed_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8, $8)
                    """,
                    user_id,
                    username,
                    (display_name or "").strip(),
                    password_hash,
                    list(normalize_roles(roles)),
                    is_active,
                    must_change_password,
                    now,
                )
        except asyncpg.UniqueViolationError as exc:
            raise UserAlreadyExists("user_id 或用户名已存在") from exc
        record = await self.get(user_id)
        assert record is not None
        return record

    async def update_password(
        self,
        user_id: str,
        password_hash: str,
        *,
        must_change_password: bool = False,
    ) -> None:
        if not password_hash:
            raise ValueError("password_hash 不能为空")
        now = datetime.now(timezone.utc)
        async with self._database.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE app_user
                SET password_hash = $2, must_change_password = $3,
                    password_changed_at = $4, updated_at = $4
                WHERE user_id = $1
                """,
                validate_user_id(user_id),
                password_hash,
                must_change_password,
                now,
            )
        if result.endswith(" 0"):
            raise UserNotFound(user_id)

    async def mark_login(self, user_id: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._database.acquire() as conn:
            await conn.execute(
                "UPDATE app_user SET last_login_at = $2 WHERE user_id = $1",
                validate_user_id(user_id),
                now,
            )

    async def set_active(self, user_id: str, is_active: bool) -> None:
        async with self._database.acquire() as conn:
            result = await conn.execute(
                "UPDATE app_user SET is_active = $2, updated_at = $3 WHERE user_id = $1",
                validate_user_id(user_id),
                is_active,
                datetime.now(timezone.utc),
            )
        if result.endswith(" 0"):
            raise UserNotFound(user_id)

    async def set_roles(self, user_id: str, roles: Iterable[str]) -> None:
        async with self._database.acquire() as conn:
            result = await conn.execute(
                "UPDATE app_user SET roles = $2, updated_at = $3 WHERE user_id = $1",
                validate_user_id(user_id),
                list(normalize_roles(roles)),
                datetime.now(timezone.utc),
            )
        if result.endswith(" 0"):
            raise UserNotFound(user_id)

    async def set_display_name(self, user_id: str, display_name: str) -> None:
        async with self._database.acquire() as conn:
            result = await conn.execute(
                "UPDATE app_user SET display_name = $2, updated_at = $3 WHERE user_id = $1",
                validate_user_id(user_id),
                display_name.strip(),
                datetime.now(timezone.utc),
            )
        if result.endswith(" 0"):
            raise UserNotFound(user_id)

    async def list_users(
        self,
        *,
        query: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UserRecord], int]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        conditions = []
        params: list = []
        if query:
            params.append(f"%{query.strip()}%")
            index = len(params)
            conditions.append(
                f"(username ILIKE ${index} OR display_name ILIKE ${index} OR user_id ILIKE ${index})"
            )
        if is_active is not None:
            params.append(is_active)
            conditions.append(f"is_active = ${len(params)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        select_sql = f"""
            SELECT user_id, username, display_name, password_hash, roles,
                   is_active, must_change_password, last_login_at,
                   created_at, updated_at, password_changed_at
            FROM app_user
            {where}
            ORDER BY created_at DESC, user_id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """
        count_sql = f"SELECT count(*) FROM app_user {where}"
        async with self._database.acquire() as conn:
            rows = await conn.fetch(select_sql, *params, limit, offset)
            total = int(await conn.fetchval(count_sql, *params))
        return [record for row in rows if (record := _row_to_record(row)) is not None], total

    async def count_users(self) -> int:
        async with self._database.acquire() as conn:
            return int(await conn.fetchval("SELECT count(*) FROM app_user"))

    async def count(self) -> int:
        """兼容旧调用方的别名。"""
        return await self.count_users()

    async def count_active_admins(self) -> int:
        async with self._database.acquire() as conn:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM app_user "
                    "WHERE is_active = TRUE AND roles @> ARRAY['admin']::text[]"
                )
            )

    # ---- 角色申请 ----
    async def create_role_request(
        self,
        *,
        request_id: str,
        user_id: str,
        requested_role: str,
        reason: str,
    ) -> RoleRequestRecord:
        if requested_role not in ("approver",):
            raise ValueError("只允许申请 approver 角色")
        now = datetime.now(timezone.utc)
        try:
            async with self._database.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO app_role_request
                        (request_id, user_id, requested_role, reason, status, created_at)
                    VALUES ($1, $2, $3, $4, 'pending', $5)
                    """,
                    request_id,
                    validate_user_id(user_id),
                    requested_role,
                    (reason or "").strip()[:500],
                    now,
                )
        except asyncpg.UniqueViolationError as exc:
            raise PendingRoleRequest("已有待审批的同类申请") from exc
        record = await self.get_role_request(request_id)
        assert record is not None
        return record

    async def get_role_request(self, request_id: str) -> RoleRequestRecord | None:
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.request_id, r.user_id, u.username, u.display_name,
                       r.requested_role, r.reason, r.status, r.reviewed_by,
                       r.review_comment, r.created_at, r.reviewed_at
                FROM app_role_request r
                JOIN app_user u ON u.user_id = r.user_id
                WHERE r.request_id = $1
                """,
                request_id,
            )
        return _row_to_role_request(row)

    async def list_role_requests(
        self, *, status: str | None = None, user_id: str | None = None
    ) -> list[RoleRequestRecord]:
        conditions = []
        params: list = []
        if status:
            params.append(status)
            conditions.append(f"r.status = ${len(params)}")
        if user_id:
            params.append(user_id)
            conditions.append(f"r.user_id = ${len(params)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._database.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT r.request_id, r.user_id, u.username, u.display_name,
                       r.requested_role, r.reason, r.status, r.reviewed_by,
                       r.review_comment, r.created_at, r.reviewed_at
                FROM app_role_request r
                JOIN app_user u ON u.user_id = r.user_id
                {where}
                ORDER BY r.created_at DESC
                LIMIT 200
                """,
                *params,
            )
        return [
            record for row in rows if (record := _row_to_role_request(row)) is not None
        ]

    async def approve_role_request(
        self,
        request_id: str,
        *,
        reviewer_id: str,
        comment: str = "",
    ) -> RoleRequestRecord:
        now = datetime.now(timezone.utc)
        async with self._database.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM app_role_request WHERE request_id = $1 FOR UPDATE",
                    request_id,
                )
                if row is None:
                    raise RoleRequestNotFound(request_id)
                if row["status"] != "pending":
                    raise RoleRequestAlreadyReviewed("申请已处理")
                if row["user_id"] == reviewer_id:
                    raise SelfApprovalError("不能审批自己的申请")
                await conn.execute(
                    """
                    UPDATE app_role_request
                    SET status = 'approved', reviewed_by = $2,
                        review_comment = $3, reviewed_at = $4
                    WHERE request_id = $1
                    """,
                    request_id,
                    reviewer_id,
                    (comment or "").strip()[:500],
                    now,
                )
                await conn.execute(
                    """
                    UPDATE app_user
                    SET roles = (
                        SELECT ARRAY(
                            SELECT DISTINCT unnest(roles || ARRAY[$2]::text[])
                        )
                    ), updated_at = $3
                    WHERE user_id = $1
                    """,
                    row["user_id"],
                    row["requested_role"],
                    now,
                )
        record = await self.get_role_request(request_id)
        assert record is not None
        return record

    async def reject_role_request(
        self,
        request_id: str,
        *,
        reviewer_id: str,
        comment: str = "",
    ) -> RoleRequestRecord:
        now = datetime.now(timezone.utc)
        async with self._database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM app_role_request WHERE request_id = $1 FOR UPDATE",
                request_id,
            )
            if row is None:
                raise RoleRequestNotFound(request_id)
            if row["status"] != "pending":
                raise RoleRequestAlreadyReviewed("申请已处理")
            if row["user_id"] == reviewer_id:
                raise SelfApprovalError("不能审批自己的申请")
            await conn.execute(
                """
                UPDATE app_role_request
                SET status = 'rejected', reviewed_by = $2,
                    review_comment = $3, reviewed_at = $4
                WHERE request_id = $1
                """,
                request_id,
                reviewer_id,
                (comment or "").strip()[:500],
                now,
            )
        record = await self.get_role_request(request_id)
        assert record is not None
        return record


def _row_to_record(row: asyncpg.Record | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
        roles=tuple(row["roles"] or ()),
        is_active=bool(row["is_active"]),
        display_name=row["display_name"] or "",
        must_change_password=bool(row["must_change_password"]),
        last_login_at=row["last_login_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        password_changed_at=row["password_changed_at"],
    )


def _row_to_role_request(row: asyncpg.Record | None) -> RoleRequestRecord | None:
    if row is None:
        return None
    return RoleRequestRecord(
        request_id=row["request_id"],
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"] or row["username"],
        requested_role=row["requested_role"],
        reason=row["reason"],
        status=row["status"],
        reviewed_by=row["reviewed_by"],
        review_comment=row["review_comment"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


_repository: UserRepository | None = None


async def get_user_repository() -> UserRepository:
    """应用级单例；首次调用时连接数据库并建表。"""
    global _repository
    if _repository is None:
        database = Database(config.AUTH_DATABASE_URL)
        await database.connect()
        repository = UserRepository(database)
        await repository.setup()
        _repository = repository
    return _repository
