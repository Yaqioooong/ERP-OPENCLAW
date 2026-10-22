import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.models import TaskRecord
from agent.procurement.task_repository import SqliteTaskRepository

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)


def make_record(task_id: str = "TASK-1", user_id: str = "u1") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        thread_id=f"procurement:{task_id}",
        conversation_id="conv-1",
        user_id=user_id,
        status="running",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_create_and_get_task(tmp_path):
    repo = SqliteTaskRepository(tmp_path / "procurement.db")
    await repo.setup()
    record = make_record()
    await repo.create(record)

    loaded = await repo.get(record.task_id)
    assert loaded.task_id == record.task_id
    assert loaded.thread_id == "procurement:TASK-1"
    assert loaded.user_id == "u1"
    assert loaded.status == "running"

    by_thread = await repo.get_by_thread("procurement:TASK-1")
    assert by_thread.task_id == record.task_id


@pytest.mark.anyio
async def test_duplicate_task_id_is_rejected(tmp_path):
    repo = SqliteTaskRepository(tmp_path / "procurement.db")
    await repo.setup()
    record = make_record()
    await repo.create(record)

    with pytest.raises(sqlite3.IntegrityError):
        await repo.create(record)


@pytest.mark.anyio
async def test_update_status_and_terminal_timestamp(tmp_path):
    repo = SqliteTaskRepository(tmp_path / "procurement.db")
    await repo.setup()
    await repo.create(make_record())

    await repo.update_status(
        task_id="TASK-1",
        status="awaiting_approval",
        current_node="request_approval",
        now=NOW + timedelta(minutes=1),
    )
    record = await repo.get("TASK-1")
    assert record.status == "awaiting_approval"
    assert record.current_node == "request_approval"
    assert record.completed_at is None

    await repo.update_status(
        task_id="TASK-1",
        status="completed",
        current_node=None,
        now=NOW + timedelta(minutes=2),
    )
    record = await repo.get("TASK-1")
    assert record.status == "completed"
    assert record.current_node == "request_approval"  # COALESCE 保留旧值
    assert record.completed_at is not None


@pytest.mark.anyio
async def test_mark_resumed_and_list(tmp_path):
    repo = SqliteTaskRepository(tmp_path / "procurement.db")
    await repo.setup()
    await repo.create(make_record("TASK-1", "u1"))
    await repo.create(make_record("TASK-2", "u2"))

    await repo.mark_resumed("TASK-1", NOW + timedelta(minutes=1))
    record = await repo.get("TASK-1")
    assert record.resumed_at is not None

    u1_tasks = await repo.list_by_user("u1")
    assert [task.task_id for task in u1_tasks] == ["TASK-1"]

    recoverable = await repo.list_recoverable()
    assert {task.task_id for task in recoverable} == {"TASK-1", "TASK-2"}

    await repo.update_status(
        task_id="TASK-1",
        status="completed",
        now=NOW + timedelta(minutes=2),
    )
    recoverable = await repo.list_recoverable()
    assert {task.task_id for task in recoverable} == {"TASK-2"}
