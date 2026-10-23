# 用protocol 把LLM 解析，MCP查询，选商，订单执行，审批仓储与图解耦，测试时可全部替换
from typing import Protocol, Any

from agent.procurement.models import ProcurementRequest, CandidateSet, ValidationIssue, PlanProposal, ApprovalRecord, \
    ApprovalDecisionResult, ProcurementPlan, OrderExecutionResult, OrderVerificationResult


class RequestParser(Protocol):
    async def parse(
            self,
            raw_text: str,
            *,
            previous: dict[str, Any] | None = None,
            answer: Any = None
    ) -> ProcurementRequest:
        # 【python小课堂】 Ellipsis 字面量，在这个函数定义中它作为函数体出现，表示 "函数体暂未实现 / 占位"
        ...

    async def merge(
            self,
            previous: ProcurementRequest,
            answer: Any
    ) -> ProcurementRequest:
        ...


class ProcurementDataProvider(Protocol):
    async def collect(self, request: ProcurementRequest) -> CandidateSet:
        ...


class PlanProposer(Protocol):
    async def propose(
            self,
            request: ProcurementRequest,
            candidates: CandidateSet,
            *,
            feedback: list[ValidationIssue],
    ) -> PlanProposal:
        ...


class ApprovalRepository(Protocol):
    async def ensure_pending(
            self,
            record: ApprovalRecord
    ) -> None:
        ...

    async def decide(
            self,
            *,
            approval_id: str,
            task_id: str,
            plan_version: int,
            plan_hash: str,
            status: str,
            decided_by: str,
            decision: str,
            comment: str | None,
            decided_by_roles: list[str] | None = None,
    ) -> ApprovalDecisionResult:
        ...

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        ...


class OrderExecutionService(Protocol):
    async def create_order(
            self,
            *,
            task_id: str,
            plan: ProcurementPlan,
            approval_id: str,
            idempotency_key: str,
    ) -> OrderExecutionResult:
        ...

    async def verify_order(
            self,
            *,
            task_id: str,
            order_id: str | None,
    ) -> OrderVerificationResult:
        ...
