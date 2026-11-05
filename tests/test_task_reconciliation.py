"""对账结果回写任务与人工复核测试（规格 specs/014）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.models import (
    ExecutionRecord,
    ExecutionStatus,
    RemoteOrder,
    TaskRecord,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
)
from agent.procurement.task_reconciliation import TaskReconciliationService
from agent.procurement.task_runner import ProcurementTaskRunner

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)


def _execution(
    status: ExecutionStatus = ExecutionStatus.UNKNOWN,
    *,
    order_id: str | None = None,
    last_error_code: str | None = None,
    requires_manual_review: bool = False,
) -> ExecutionRecord:
    return ExecutionRecord(
        idempotency_key="key-1",
        task_id="TASK-1",
        plan_version=1,
        plan_hash="hash",
        approval_id="APR-1",
        status=status,
        order_id=order_id,
        order_number="PO-1",
        requires_manual_review=requires_manual_review,
        last_error_code=last_error_code,
        last_error_message="message",
        created_at=NOW,
        updated_at=NOW,
    )


def _task(status: str = TaskStatus.AWAITING_RETRY.value) -> TaskRecord:
    return TaskRecord(
        task_id="TASK-1",
        thread_id="procurement:TASK-1",
        user_id="u-1",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


class _TaskRepo:
    def __init__(self, task: TaskRecord | None = None):
        self.record = task or _task()
        self.apply_calls = 0

    async def get(self, task_id: str):
        return self.record if task_id == self.record.task_id else None

    async def update_status(self, **kwargs):
        return None

    async def apply_reconciliation(self, **kwargs) -> bool:
        self.apply_calls += 1
        update = {
            "status": kwargs["status"],
            "order_id": kwargs.get("order_id"),
            "requires_manual_review": kwargs.get(
                "requires_manual_review", False
            ),
            "execution_key": kwargs.get("execution_key"),
            "last_error_code": (
                kwargs.get("error") or {}
            ).get("code"),
            "last_error_message": (
                kwargs.get("error") or {}
            ).get("message"),
            "reconciled_at": kwargs.get("now") or NOW,
        }
        self.record = self.record.model_copy(update=update)
        return True

    async def list_recoverable(self, **kwargs):
        return [self.record]

    async def mark_resumed(self, **kwargs):
        return None


class _Events:
    def __init__(self):
        self.events: list[dict] = []

    async def emit(self, **kwargs):
        self.events.append(kwargs)


def _service(repo: _TaskRepo) -> tuple[TaskReconciliationService, _Events]:
    events = _Events()
    return (
        TaskReconciliationService(
            tasks=repo, events=events, now=lambda: NOW
        ),
        events,
    )


@pytest.mark.anyio
async def test_succeeded_marks_task_completed_with_order_id():
    repo = _TaskRepo()
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.SUCCEEDED,
        order=RemoteOrder(order_id="PO-9", order_number="PO-1"),
    )

    changed = await service.apply_verification(_execution(), result)

    assert changed is True
    assert repo.record.status == TaskStatus.COMPLETED.value
    assert repo.record.order_id == "PO-9"
    assert repo.record.last_error_code is None
    assert events.events[0]["event_type"].value == "task.reconciled"


@pytest.mark.anyio
async def test_mismatch_marks_awaiting_review():
    repo = _TaskRepo()
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.MISMATCH,
        order=RemoteOrder(order_id="PO-9", order_number="PO-1"),
        message="数量不一致",
    )

    changed = await service.apply_verification(_execution(), result)

    assert changed is True
    assert repo.record.status == TaskStatus.AWAITING_REVIEW.value
    assert repo.record.requires_manual_review is True
    assert repo.record.last_error_code == "ORDER_CONTENT_MISMATCH"
    assert events.events


@pytest.mark.anyio
async def test_cancelled_marks_awaiting_review():
    repo = _TaskRepo()
    service, _ = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.CANCELLED,
        order=RemoteOrder(order_id="PO-9", order_number="PO-1"),
        message="远端取消",
    )

    await service.apply_verification(_execution(), result)

    assert repo.record.status == TaskStatus.AWAITING_REVIEW.value
    assert repo.record.last_error_code == "ORDER_CANCELLED_REMOTE"


@pytest.mark.anyio
async def test_not_created_is_non_terminal():
    repo = _TaskRepo()
    service, _ = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.NOT_CREATED,
        message="确认远端未创建",
    )

    await service.apply_verification(_execution(), result)

    assert repo.record.status == TaskStatus.NOT_CREATED.value
    assert repo.record.status not in {
        TaskStatus.FAILED.value,
        TaskStatus.COMPLETED.value,
    }
    assert repo.record.last_error_code == "ORDER_NOT_FOUND_CONFIRMED"


@pytest.mark.anyio
async def test_unknown_does_not_change_task_or_emit():
    repo = _TaskRepo()
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.UNKNOWN,
        message="仍未知",
    )

    changed = await service.apply_verification(_execution(), result)

    assert changed is False
    assert repo.apply_calls == 0
    assert events.events == []


@pytest.mark.anyio
async def test_unavailable_does_not_change_task_or_emit():
    repo = _TaskRepo()
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.UNAVAILABLE,
        message="订单服务 502",
    )

    changed = await service.apply_verification(_execution(), result)

    assert changed is False
    assert repo.apply_calls == 0
    assert events.events == []


@pytest.mark.anyio
async def test_repeated_result_is_idempotent():
    repo = _TaskRepo()
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.SUCCEEDED,
        order=RemoteOrder(order_id="PO-9", order_number="PO-1"),
    )

    first = await service.apply_verification(_execution(), result)
    second = await service.apply_verification(_execution(), result)

    assert first is True
    assert second is False
    assert len(events.events) == 1


@pytest.mark.anyio
async def test_manual_success_and_not_created_map_to_task():
    repo = _TaskRepo()
    service, _ = _service(repo)

    changed = await service.apply_execution_record(
        _execution(ExecutionStatus.SUCCEEDED, order_id="PO-9")
    )
    assert changed is True
    assert repo.record.status == TaskStatus.COMPLETED.value
    assert repo.record.order_id == "PO-9"

    repo.record = _task()
    changed = await service.apply_execution_record(
        _execution(
            ExecutionStatus.FAILED,
            last_error_code="ORDER_NOT_FOUND_CONFIRMED",
        )
    )
    assert changed is True
    assert repo.record.status == TaskStatus.NOT_CREATED.value


class _Graph:
    def __init__(self, values: dict, next_nodes: list[str] | None = None):
        self.values = values
        self.next_nodes = next_nodes or []
        self.calls = 0

    async def aget_state(self, config):
        self.calls += 1
        return SimpleNamespace(
            values=self.values,
            next=self.next_nodes,
            interrupts=[],
        )


@pytest.mark.anyio
async def test_snapshot_uses_record_for_awaiting_review():
    repo = _TaskRepo(_task(TaskStatus.AWAITING_REVIEW.value))
    repo.record = repo.record.model_copy(
        update={
            "requires_manual_review": True,
            "execution_key": "key-1",
            "last_error_code": "ORDER_CONTENT_MISMATCH",
        }
    )
    graph = _Graph({"status": "failed", "order_id": None})
    runner = ProcurementTaskRunner(
        graph=graph,
        task_repository=repo,
        now=lambda: NOW,
    )

    result = await runner.get(task_id="TASK-1", user_id="u-1")

    assert result.status == TaskStatus.AWAITING_REVIEW.value
    assert result.state_summary["requires_manual_review"] is True
    assert result.state_summary["execution_key"] == "key-1"
    assert result.state_summary["last_error"]["code"] == "ORDER_CONTENT_MISMATCH"


@pytest.mark.anyio
async def test_reconcile_skips_manual_review_status():
    repo = _TaskRepo(_task(TaskStatus.NOT_CREATED.value))
    graph = _Graph({"status": "failed"})
    runner = ProcurementTaskRunner(
        graph=graph,
        task_repository=repo,
        now=lambda: NOW,
    )

    checked = await runner.reconcile()

    assert checked == 1
    assert graph.calls == 0
    assert repo.record.status == TaskStatus.NOT_CREATED.value


@pytest.mark.anyio
async def test_postgres_task_reconciliation_roundtrip(db):
    from agent.procurement.task_repository import PostgresTaskRepository

    repo = PostgresTaskRepository(db)
    await repo.create(_task(TaskStatus.AWAITING_RETRY.value))

    changed = await repo.apply_reconciliation(
        task_id="TASK-1",
        status=TaskStatus.COMPLETED.value,
        order_id="PO-9",
        requires_manual_review=False,
        execution_key="key-1",
        error=None,
        now=NOW,
    )
    assert changed is True

    loaded = await repo.get("TASK-1")
    assert loaded is not None
    assert loaded.status == TaskStatus.COMPLETED.value
    assert loaded.order_id == "PO-9"
    assert loaded.execution_key == "key-1"
    assert loaded.reconciled_at == NOW
    assert loaded.completed_at == NOW

    # 确认未创建是非终态，可再次人工回转。
    changed = await repo.apply_reconciliation(
        task_id="TASK-1",
        status=TaskStatus.NOT_CREATED.value,
        order_id=None,
        requires_manual_review=False,
        execution_key="key-1",
        error={
            "code": "ORDER_NOT_FOUND_CONFIRMED",
            "message": "确认未创建",
        },
        now=NOW,
    )
    assert changed is True
    loaded = await repo.get("TASK-1")
    assert loaded.status == TaskStatus.NOT_CREATED.value
    assert loaded.completed_at is None


def test_manual_review_permissions():
    from api_view.api.procurement import _can_manage_task
    from api_view.auth.models import AuthenticatedUser

    task = _task()
    owner = AuthenticatedUser(user_id="u-1", username="owner", roles=("requester",))
    approver = AuthenticatedUser(
        user_id="u-2", username="approver", roles=("approver",)
    )
    admin = AuthenticatedUser(user_id="u-3", username="admin", roles=("admin",))
    stranger = AuthenticatedUser(
        user_id="u-4", username="stranger", roles=("requester",)
    )

    assert _can_manage_task(task, owner) is True
    assert _can_manage_task(task, approver) is True
    assert _can_manage_task(task, admin) is True
    assert _can_manage_task(task, stranger) is False


def test_frontend_review_entry_is_wired():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend/src/components/ProcurementTaskPanel.vue").read_text(
        encoding="utf-8"
    )
    events = (root / "frontend/src/api/taskEvents.js").read_text(encoding="utf-8")
    store = (root / "frontend/src/composables/useTaskStore.js").read_text(
        encoding="utf-8"
    )
    card = (root / "frontend/src/components/TaskCard.vue").read_text(
        encoding="utf-8"
    )

    assert "订单结果需要人工复核" in panel
    assert "resolveExecution" in panel
    assert "requiresReview" in panel
    assert "reviewResolved" in panel
    assert "task_synced" in panel
    assert "status === 'not_created'" in panel
    assert "cancelledMessage" in panel
    assert "订单结果仍未确认" in panel
    assert "task.reconciled" in events
    assert "task.reconciled" in store
    assert "awaiting_review" in card
    assert "not_created" in card


def test_not_created_snapshot_clears_manual_review_flag():
    runner = ProcurementTaskRunner(graph=None, task_repository=None)

    result = runner._record_snapshot_result(
        _task(TaskStatus.NOT_CREATED.value),
        {
            "execution_status": ExecutionStatus.UNKNOWN.value,
            "requires_manual_review": True,
        },
        "procurement:TASK-1",
    )

    assert result.status == TaskStatus.NOT_CREATED.value
    assert result.state_summary["execution_status"] == "failed"
    assert result.state_summary["requires_manual_review"] is False


@pytest.mark.anyio
async def test_completed_task_is_not_downgraded_by_late_not_created():
    repo = _TaskRepo(_task(TaskStatus.COMPLETED.value))
    repo.record = repo.record.model_copy(update={"order_id": "PO-9"})
    service, events = _service(repo)
    result = VerificationResult(
        outcome=VerificationOutcome.NOT_CREATED,
        message="迟到的未创建结果",
    )

    changed = await service.apply_verification(_execution(), result)

    assert changed is False
    assert repo.record.status == TaskStatus.COMPLETED.value
    assert repo.record.order_id == "PO-9"
    assert events.events == []
