# 用protocol 把LLM 解析，MCP查询，选商，订单执行，审批仓储与图解耦，测试时可全部替换
from dataclasses import dataclass
from typing import Protocol, Any

from agent.procurement.models import ProcurementRequest, RequestedItem, CandidateSet, ValidationIssue, PlanProposal, ApprovalRecord, \
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
    async def resolve_part(self, item: RequestedItem) -> "PartResolution":
        ...

    async def collect(self, request: ProcurementRequest) -> CandidateSet:
        ...


@dataclass(frozen=True)
class PartResolution:
    id: int
    name: str
    code: str | None = None


class PartResolutionError(ValueError):
    """物料无法唯一解析；调用方应转成待补充，而不是任务失败。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        candidates: list[dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.candidates = candidates or []
        super().__init__(message)


@dataclass(frozen=True)
class SupplierResolution:
    id: int
    name: str
    status: int | None = None


class SupplierResolutionError(ValueError):
    """供应商无法唯一解析；调用方应转成待补充，而不是任务崩溃。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        candidates: list[dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.candidates = candidates or []
        super().__init__(message)


class SupplierResolver(Protocol):
    async def resolve(
        self,
        *,
        supplier_id: int | None,
        supplier_name: str | None,
    ) -> SupplierResolution:
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
