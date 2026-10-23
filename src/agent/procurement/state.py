import operator
from typing import TypedDict, Annotated, Any

from agent.procurement.models import ApprovalStatus, ExecutionStatus, TaskStatus


class ProcurementState(TypedDict, total=False):
    # 【python小课堂】total=False 将该 TypedDict 中定义的所有键都设为“非必需”（即可以省略），只可用于TypedDict
    # ---- 任务标识 ----
    task_id: str
    conversation_id: str | None
    user_id: str
    username: str

    # ---- 需求 ----
    raw_request: str
    request: dict[str, Any]
    missing_fields: list[str]
    clarification_round: int
    clarification_answer: dict[str, Any] | None

    # ---- 查询与分析 ----
    inventory_data: list[dict[str, Any]]
    supplier_candidates: list[dict[str, Any]]
    procurement_plan: dict[str, Any] | None
    plan_version: int
    plan_hash: str | None

    # ---- 校验 ----
    validation_errors: list[dict[str, Any]]
    validation_warnings: list[dict[str, Any]]
    validation_passed: bool
    retry_count: int

    # ---- 审批 ----
    approval_status: str  # ApprovalStatus
    approval_id: str | None
    approval_decision: dict[str, Any] | None
    # 发起审批时捕获的方案快照：恢复/执行前用它判断“批的还是不是当前方案”
    approval_plan_version: int | None
    approval_plan_hash: str | None
    approval_plan_snapshot: dict[str, Any] | None
    approved_plan_version: int | None
    approved_plan_hash: str | None
    approved_by: str | None
    approved_at: str | None
    approval_expires_at: str | None

    # ---- 执行 ----
    idempotency_key: str | None
    order_id: str | None
    execution_status: str  # ExecutionStatus
    execution_attempts: int
    requires_manual_review: bool
    last_error: dict[str, Any] | None

    # ---- 诊断 ----
    status: str  # TaskStatus
    node_history: Annotated[list[str], operator.add]
    created_at: str
    updated_at: str

    # ---- 恢复 ----
    retry_from: str | None  # analyze_procurement / verify_order
    failure_stage: str | None  # 失败发生在哪个节点
    resume_count: int  # 恢复次数（防止无限恢复）
    last_checkpoint_id: str | None  # 可选：最近一次快照 ID（诊断）


def initial_state(
        *,
        task_id: str,
        raw_request: str,
        user_id: str,
        username: str,
        conversation_id: str | None = None,
        request: dict[str, Any] | None = None,
        now: str
) -> ProcurementState:
    # 【python小课堂】*之后的所有参数必须通过关键字（key = value）方式传递，不能通过位置方式传递
    """创建一个新采购任务的初始状态"""
    return {
        "task_id": task_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "username": username,
        "raw_request": raw_request,
        "request": request or {},
        "missing_fields": [],
        "clarification_round": 0,
        "clarification_answer": None,
        "inventory_data": [],
        "supplier_candidates": [],
        "procurement_plan": None,
        "plan_version": 0,
        "plan_hash": None,
        "validation_errors": [],
        "validation_warnings": [],
        "validation_passed": False,
        "retry_count": 0,
        "approval_status": ApprovalStatus.NOT_REQUIRED.value,
        "approval_id": None,
        "approval_decision": None,
        "approval_plan_version": None,
        "approval_plan_hash": None,
        "approval_plan_snapshot": None,
        "approved_plan_version": None,
        "approved_plan_hash": None,
        "approved_by": None,
        "approved_at": None,
        "approval_expires_at": None,
        "idempotency_key": None,
        "order_id": None,
        "execution_status": ExecutionStatus.NOT_STARTED.value,
        "execution_attempts": 0,
        "requires_manual_review": False,
        "last_error": None,
        "retry_from": None,
        "failure_stage": None,
        "resume_count": 0,
        "last_checkpoint_id": None,
        "status": TaskStatus.NEW.value,
        "node_history": [],
        "created_at": now,
        "updated_at": now,
    }
