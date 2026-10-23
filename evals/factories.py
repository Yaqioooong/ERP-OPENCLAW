from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from agent.procurement.models import (
    ApprovalDecisionResult,
    ApprovalRecord,
    ApprovalStatus,
    CandidateSet,
    OrderExecutionResult,
    OrderVerificationResult,
    ProcurementRequest,
    TaskRecord,
    TaskStatus,
    ToolError,
)
from agent.procurement.models import ExecutionStatus

TZ = ZoneInfo("Asia/Shanghai")
FIXED_NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


class StaticParser:
    def __init__(self, request: ProcurementRequest):
        self._request = request

    async def parse(self, raw_text, *, previous=None, answer=None):
        return self._request

    async def merge(self, previous, answer):
        patch = (answer or {}).get("request_patch") or {}
        data = previous.model_dump(mode="json")
        data.update(patch)
        return ProcurementRequest.model_validate(data)


class StaticProvider:
    def __init__(self, candidates: CandidateSet):
        self._candidates = candidates
        self.calls = 0

    async def collect(self, request):
        self.calls += 1
        return self._candidates


class StaticProposer:
    async def propose(self, request, candidates, *, feedback):
        from agent.procurement.models import PlanProposal, PlanProposalItem

        return PlanProposal(
            supplier_id=4,
            items=[
                PlanProposalItem(
                    part_id=item.part_id or 1,
                    quantity=item.quantity or 10,
                )
                for item in request.items
            ],
            rationale="评测固定方案",
        )


class MemoryApprovalRepository:
    def __init__(self):
        self.records: dict[str, ApprovalRecord] = {}

    async def ensure_pending(self, record: ApprovalRecord) -> None:
        self.records.setdefault(record.approval_id, record)

    async def decide(self, **kwargs) -> ApprovalDecisionResult:
        record = self.records.get(kwargs["approval_id"])
        if record is None:
            return ApprovalDecisionResult.STALE
        if (
            record.task_id != kwargs["task_id"]
            or record.plan_version != kwargs["plan_version"]
            or record.plan_hash != kwargs["plan_hash"]
            or record.status != ApprovalStatus.PENDING
        ):
            if record.status.value == kwargs["status"]:
                return ApprovalDecisionResult.ALREADY_APPLIED
            return ApprovalDecisionResult.STALE
        self.records[record.approval_id] = record.model_copy(
            update={
                "status": kwargs["status"],
                "approved_by": kwargs["decided_by"],
                "decision": kwargs["decision"],
                "comment": kwargs["comment"],
                "decided_at": FIXED_NOW,
            }
        )
        return ApprovalDecisionResult.APPLIED

    async def get(self, approval_id: str):
        return self.records.get(approval_id)


class MemoryTaskRepository:
    def __init__(self):
        self.records: dict[str, TaskRecord] = {}

    async def setup(self):
        return None

    async def create(self, record: TaskRecord):
        if record.task_id in self.records:
            raise ValueError("duplicate task")
        self.records[record.task_id] = record

    async def get(self, task_id: str):
        return self.records.get(task_id)

    async def get_by_thread(self, thread_id: str):
        return next(
            (r for r in self.records.values() if r.thread_id == thread_id),
            None,
        )

    async def update_status(
        self,
        *,
        task_id: str,
        status: str,
        current_node: str | None = None,
        retry_from: str | None = None,
        error: dict | None = None,
        now: datetime | None = None,
    ):
        record = self.records[task_id]
        update = {
            "status": status,
            "updated_at": now or FIXED_NOW,
        }
        if current_node is not None:
            update["current_node"] = current_node
        if retry_from is not None:
            update["retry_from"] = retry_from
        if error is not None:
            update["last_error_code"] = error.get("code")
            update["last_error_message"] = error.get("message")
        if status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.REJECTED.value,
            TaskStatus.CANCELLED.value,
        }:
            update["completed_at"] = now or FIXED_NOW
        self.records[task_id] = record.model_copy(update=update)

    async def mark_resumed(self, task_id: str, now: datetime):
        self.records[task_id] = self.records[task_id].model_copy(
            update={"resumed_at": now, "updated_at": now}
        )

    async def list_recoverable(self, *, limit: int = 200):
        return list(self.records.values())[:limit]

    async def list_by_user(self, user_id: str, *, limit: int = 50):
        return [
            record for record in self.records.values()
            if record.user_id == user_id
        ][:limit]


class FakeOrderExecutor:
    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.create_calls = 0
        self.verify_calls = 0
        self.orders: list[str] = []

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        self.create_calls += 1
        if self.mode == "timeout":
            raise TimeoutError("order create timeout")
        if self.mode == "fail":
            return OrderExecutionResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="ORDER_BUSINESS_REJECTED",
                    retryable=False,
                    message="mock business reject",
                ),
            )
        order_number = f"PO-TEST-{self.create_calls:03d}"
        self.orders.append(order_number)
        return OrderExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id=str(100 + self.create_calls),
            order_number=order_number,
        )

    async def verify_order(self, *, task_id, order_id):
        self.verify_calls += 1
        if self.mode in {"ok", "timeout"}:
            return OrderVerificationResult(
                status=ExecutionStatus.SUCCEEDED,
                order_id=order_id or "100",
                order_number="PO-TEST-001",
            )
        return OrderVerificationResult(
            status=ExecutionStatus.FAILED,
            error=ToolError(
                code="ORDER_NOT_FOUND_CONFIRMED",
                retryable=False,
                message="mock not found",
            ),
        )
