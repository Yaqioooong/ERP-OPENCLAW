from __future__ import annotations

import pytest

from api_view.auth.passwords import hash_password
from api_view.auth.users import UserAlreadyExists, UserRepository


@pytest.mark.anyio
async def test_create_and_lookup_user(db):
    repo = UserRepository(db)
    await repo.setup()

    created = await repo.create(
        user_id="u_1001",
        username="zhangsan",
        password_hash=hash_password("password-123"),
        roles=("requester", "approver"),
    )
    assert created.user_id == "u_1001"
    assert created.is_active is True

    by_name = await repo.get_by_username("ZhangSan")
    assert by_name is not None
    assert by_name.user_id == "u_1001"
    assert by_name.roles == ("requester", "approver")

    assert await repo.count() == 1
    records, total = await repo.list_users()
    assert total == 1
    assert [r.user_id for r in records] == ["u_1001"]


@pytest.mark.anyio
async def test_duplicate_user_is_rejected(db):
    repo = UserRepository(db)
    await repo.setup()
    await repo.create(
        user_id="u_1001",
        username="zhangsan",
        password_hash=hash_password("password-123"),
    )
    with pytest.raises(UserAlreadyExists):
        await repo.create(
            user_id="u_1001",
            username="other",
            password_hash=hash_password("password-123"),
        )
    with pytest.raises(UserAlreadyExists):
        await repo.create(
            user_id="u_1002",
            username="ZHANGSAN",
            password_hash=hash_password("password-123"),
        )


@pytest.mark.anyio
async def test_update_password_and_invalid_user_id(db):
    repo = UserRepository(db)
    await repo.setup()
    await repo.create(
        user_id="u_1001",
        username="zhangsan",
        password_hash=hash_password("password-123"),
    )
    await repo.update_password("u_1001", hash_password("new-password-456"))
    record = await repo.get("u_1001")
    assert record is not None

    with pytest.raises(KeyError):
        await repo.update_password("u_9999", hash_password("new-password-456"))

    with pytest.raises(Exception):
        await repo.create(
            user_id="../escape",
            username="bad",
            password_hash=hash_password("password-123"),
        )


@pytest.mark.anyio
async def test_role_request_and_admin_count(db):
    repo = UserRepository(db)
    await repo.setup()
    await repo.create(
        user_id="u_admin",
        username="admin",
        display_name="管理员",
        password_hash=hash_password("password-123"),
        roles=("admin",),
    )
    await repo.create(
        user_id="u_user",
        username="user",
        display_name="用户",
        password_hash=hash_password("password-123"),
    )
    assert await repo.count_active_admins() == 1

    request = await repo.create_role_request(
        request_id="rr_test",
        user_id="u_user",
        requested_role="approver",
        reason="需要审批",
    )
    assert request.status == "pending"

    pending = await repo.list_role_requests(status="pending")
    assert [r.request_id for r in pending] == ["rr_test"]

    approved = await repo.approve_role_request(
        "rr_test", reviewer_id="u_admin", comment="同意"
    )
    assert approved.status == "approved"
    user = await repo.get("u_user")
    assert user is not None
    assert "approver" in user.roles

    records, total = await repo.list_users(query="用户")
    assert total == 1
    assert records[0].user_id == "u_user"
