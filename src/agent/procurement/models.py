from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============================================================
# 1. 枚举
# ============================================================
class TaskStatus(StrEnum):
    NEW = "new"
    PARSING = "parsing"
    NEED_INFO = "need_info"
    ANALYZING = "analyzing"
    PLAN_READY = "plan_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    APPROVED_PENDING_EXECUTION = "approved_pending_execution"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RUNNING = "running"
    AWAITING_RETRY = "awaiting_retry"
    ORPHANED = "orphaned"  # 孤儿


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"
    STALE = "stale"


class ExecutionStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NEEDS_REVIEW = "needs_review"


class DecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class ApprovalDecisionResult(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"


class VerificationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_CREATED = "not_created"
    CANCELLED = "cancelled"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


# ============================================================
# 2. 需求
# ============================================================
class RequestedItem(BaseModel):
    """一条采购需求。字段全部可选，由validate_request 判断缺少什么"""
    model_config = ConfigDict(extra="ignore")  # 当你在创建模型实例时，如果传入了模型未定义的额外字段，Pydantic 会静默忽略这些字段，不会报错，也不会将它们保存到模型实例中。

    part_id: int | None = Field(None, description="零部件 ID （如果用户明确给出）")
    part_name: str | None = Field(None, description="零部件名称/型号，保留用户原文")
    part_code: str | None = Field(None, description="零部件编码，如PC2001")
    quantity: int | None = Field(None, description="采购数量；合法性由规则层校验")
    target_unit_price: Decimal | None = Field(None, description="用户给出的目标单价；不是最终成交价")


class ProcurementRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[RequestedItem] = Field(default_factory=list, description="采购物料列表；至少一条")
    warehouse_location: str | None = Field(None, description="收货/目标仓库位置，如“华东仓”“B区-1号库”")
    budget: Decimal | None = Field(None, description="本任务预算上限，CNY")
    required_by: date | None = Field(None, description="要求到货日期")
    preferred_supplier_id: int | None = Field(None, description="用户指定供应商")
    priority: Literal["price", "lead_time", "balanced"] = Field(
        "balanced", description="选商偏好"
    )
    note: str | None = Field(None, description="用户补充说明")


# ============================================================
# 3. 数据快照 / 候选供应商
# ============================================================
class InventorySnapshot(BaseModel):
    part_id: int
    part_name: str
    warehouse_location: str | None = None
    current_quantity: int | None = None
    safety_stock: int | None = None


class SupplierOffer(BaseModel):
    """一个“供应商 × 物料”的候选报价。"""

    supplier_id: int
    supplier_name: str
    supplier_status: int | None = None
    part_id: int
    part_name: str
    unit_price: Decimal | None = None
    price_source: Literal[
                      "historical_order", "part_master", "supplier_quote"
                  ] | None = None
    lead_time_days: int | None = None
    available_quantity: int | None = None
    credit_rating: str | None = None
    data_warnings: list[str] = Field(default_factory=list)


class CandidateSet(BaseModel):
    offers: list[SupplierOffer] = Field(default_factory=list)
    inventory: list[InventorySnapshot] = Field(default_factory=list)
    data_warnings: list[str] = Field(default_factory=list)


# ============================================================
# 4. 方案（由代码构建，不由模型直接写金额）
# ============================================================
class PlanItem(BaseModel):
    part_id: int
    part_name: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    subtotal: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    price_source: Literal["historical_order", "part_master", "supplier_quote"]
    lead_time_days: int | None = None


class InventoryGap(BaseModel):
    part_id: int
    requested_quantity: int
    available_quantity: int | None = None
    gap: int | None = None


class ProcurementPlan(BaseModel):
    plan_id: str
    task_id: str
    version: int = Field(ge=1)
    supplier_id: int
    supplier_name: str
    warehouse_location: str
    items: list[PlanItem] = Field(min_length=1)
    total_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: Literal["CNY"] = "CNY"
    expected_delivery_date: date | None = None
    inventory_gaps: list[InventoryGap] = Field(default_factory=list)
    budget: Decimal | None = None
    rationale: str = ""
    created_at: datetime


class PlanProposalItem(BaseModel):
    """模型/启发式只负责选物料和数量，价格由候选数据回填。"""

    part_id: int
    quantity: int = Field(gt=0)


class PlanProposal(BaseModel):
    supplier_id: int
    items: list[PlanProposalItem] = Field(min_length=1)
    rationale: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)


# ============================================================
# 5. 校验
# ============================================================
class ValidationIssue(BaseModel):
    code: str
    field: str | None = None
    message: str
    severity: Literal["error", "warning"] = "error"
    retryable: bool = False
    fixable: bool = False


class PlanValidationResult(BaseModel):
    passed: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    plan_hash: str
    recomputed_total: Decimal


# ============================================================
# 6. 审批
# ============================================================
class ApprovalRecord(BaseModel):
    approval_id: str
    task_id: str
    plan_version: int
    plan_hash: str
    status: ApprovalStatus
    requested_by: str
    approved_by: str | None = None
    decision: str | None = None
    comment: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    plan_snapshot: dict[str, Any] | None = None
    analysis_report: str | None = None
    approved_by_roles: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """来自前端/恢复请求的决策。注意：不信任客户端传的 version/hash。"""

    model_config = ConfigDict(extra="ignore")

    decision: DecisionType
    comment: str | None = None
    changes: dict | None = Field(
        None, description="decision=modify 时的需求补丁，只允许改需求字段"
    )

    @model_validator(mode='before')
    @classmethod
    def _from_legacy_hitl_payload(cls, data):
        """兼容现有前端/order_guard 的 {"decisions":[{"type":"approve"}]}。"""
        if isinstance(data, dict) and "decision" not in data and "decisions" in data:
            decisions = data.get("decisions") or []
            first = (
                decisions[0]
                if decisions and isinstance(decisions[0], dict)
                else {}
            )
            return {
                "decision": first.get("type") or "reject",
                "comment": first.get("message") or data.get("comment"),
                "changes": first.get("changes") or data.get("changes"),
            }
        return data


class ClarificationAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_patch: dict | None = None
    text: str | None = None


# ============================================================
# 7. 执行结果（端口返回）
# ============================================================
class ToolError(BaseModel):
    code: str
    retryable: bool = False
    message: str


class OrderExecutionResult(BaseModel):
    status: ExecutionStatus
    order_id: str | None = None
    order_number: str | None = None
    error: ToolError | None = None


class OrderVerificationResult(BaseModel):
    status: ExecutionStatus
    order_id: str | None = None
    order_number: str | None = None
    error: ToolError | None = None
    requires_manual_review: bool = False


class TaskRecord(BaseModel):
    task_id: str
    thread_id: str
    conversation_id: str | None = None
    user_id: str
    status: str
    current_node: str | None = None
    retry_from: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    resumed_at: datetime | None = None
    completed_at: datetime | None = None


class TaskRunResult(BaseModel):
    task_id: str
    thread_id: str
    status: str
    current_node: str | None = None
    interrupt: dict[str, Any] | None = None
    state_summary: dict[str, Any] = {}
    recoverable: bool = False


class ExecutionRecord(BaseModel):
    """一笔逻辑订单的执行事实"""

    idempotency_key: str
    task_id: str
    plan_version: int
    plan_hash: str
    approval_id: str
    status: ExecutionStatus

    plan_snapshot: dict[str, Any] | None = None
    order_id: str | None = None
    order_number: str | None = None

    attempt_count: int = 0
    verification_attempts: int = 0
    requires_manual_review: bool = False
    remote_status: int | None = None

    last_error_code: str | None = None
    last_error_message: str | None = None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    verification_payload: dict[str, Any] | None = None

    lease_expires_at: datetime | None = None
    reconcile_lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    executed_at: datetime | None = None
    last_verified_at: datetime | None = None


class RemoteOrderItem(BaseModel):
    part_id: int
    quantity: int
    unit_price: Decimal | None = None
    subtotal: Decimal | None = None


class RemoteOrder(BaseModel):
    order_id: str
    order_number: str
    status: int | None = None
    total_amount: Decimal | None = None
    expected_delivery_date: date | None = None
    supplier_id: int | None = None
    items: list[RemoteOrderItem] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    outcome: VerificationOutcome
    order: RemoteOrder | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    message: str = ""
    attempts: int = 0
