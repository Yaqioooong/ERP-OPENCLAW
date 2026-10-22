from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.procurement.approval_repository import SqliteApprovalRepository
from agent.procurement.dependencies import ProcurementDependencies
from agent.procurement.models import (
    CandidateSet,
    ExecutionStatus,
    OrderExecutionResult,
    OrderVerificationResult,
    PlanProposal,
    PlanProposalItem,
    ProcurementRequest,
    RequestedItem,
    ToolError,
)
from agent.procurement.planner import DefaultPlanBuilder
from agent.procurement.state import initial_state
from agent.schema import ProcurementContext
from tests.factories import make_candidates

TZ = ZoneInfo("Asia/Shanghai")
FIXED_NOW = datetime(2026, 10, 21, 10, 0, tzinfo=TZ)


def make_context(user_id: str = "u1", username: str = "张三") -> ProcurementContext:
    return ProcurementContext(user_id=user_id, username=username)


def make_config(task_id: str = "TASK-1") -> dict:
    return {"configurable": {"thread_id": f"procurement:{task_id}"}}


def make_state(
        *,
        task_id: str = "TASK-1",
        raw_request: str = "为华东仓补充 200 个活塞环组件",
        request: ProcurementRequest | None = None,
) -> dict:
    return initial_state(
        task_id=task_id,
        raw_request=raw_request,
        user_id="u1",
        username="张三",
        request=request.model_dump(mode="json") if request else None,
        now=FIXED_NOW.isoformat(),
    )


def make_incomplete_request() -> ProcurementRequest:
    """缺 warehouse_location 的需求，用于追问测试。"""
    return ProcurementRequest(
        items=[RequestedItem(part_id=1, part_name="活塞环组件", quantity=200)],
        budget=Decimal("50000.00"),
        required_by=date(2026, 11, 1),
    )


class StaticRequestParser:
    """解析结果固定；补充信息用 request_patch 做确定性合并。"""

    def __init__(self, request: ProcurementRequest | None = None):
        self._request = request or ProcurementRequest(
            items=[RequestedItem(part_id=1, part_name="活塞环组件", quantity=200)],
            warehouse_location="华东仓",
            budget=Decimal("50000.00"),
            required_by=date(2026, 11, 1),
        )

    async def parse(self, raw_text, *, previous=None, answer=None):
        return self._request.model_copy(deep=True)

    async def merge(self, previous, answer):
        if isinstance(answer, dict) and isinstance(answer.get("request_patch"), dict):
            data = previous.model_dump(mode="json")
            _deep_merge(data, answer["request_patch"])
            return ProcurementRequest.model_validate(data)
        return previous.model_copy(deep=True)


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


class StaticDataProvider:
    def __init__(self, candidates: CandidateSet | None = None):
        self._candidates = candidates or make_candidates()

    async def collect(self, request: ProcurementRequest) -> CandidateSet:
        return self._candidates.model_copy(deep=True)


class StaticPlanProposer:
    def __init__(self, supplier_id: int = 4):
        self._supplier_id = supplier_id

    async def propose(self, request, candidates, *, feedback):
        items = [
            PlanProposalItem(part_id=item.part_id, quantity=item.quantity)
            for item in request.items
            if item.part_id is not None and item.quantity is not None
        ]
        if not items:
            raise ValueError("request has no resolvable part_id/quantity")
        return PlanProposal(
            supplier_id=self._supplier_id,
            items=items,
            rationale="测试方案",
            confidence=1.0,
        )


class FakeOrderExecutor:
    def __init__(self, *, success: bool = True):
        self.success = success
        self.create_calls = []
        self.verify_calls = []

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        self.create_calls.append(
            {
                "task_id": task_id,
                "plan": plan,
                "approval_id": approval_id,
                "idempotency_key": idempotency_key,
            }
        )
        if not self.success:
            return OrderExecutionResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(code="FAKE_FAILED", message="模拟失败"),
            )
        return OrderExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id="PO-TEST-001",
            order_number="PO20261021001",
        )

    async def verify_order(self, *, task_id, order_id):
        self.verify_calls.append({"task_id": task_id, "order_id": order_id})
        return OrderVerificationResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id=order_id,
            order_number="PO20261021001",
        )


def make_test_deps(
        order_executor,
        *,
        db_path: str | Path,
        parser=None,
        data_provider=None,
        proposer=None,
        execution_enabled: bool = False,
) -> ProcurementDependencies:
    return ProcurementDependencies(
        request_parser=parser or StaticRequestParser(),
        data_provider=data_provider or StaticDataProvider(),
        plan_proposer=proposer or StaticPlanProposer(),
        plan_builder=DefaultPlanBuilder(),
        approval_repository=SqliteApprovalRepository(db_path),
        order_executor=order_executor,
        now=lambda: FIXED_NOW,
        execution_enabled=execution_enabled,
    )
