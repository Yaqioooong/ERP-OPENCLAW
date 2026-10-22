"""路由函数只读 state，不产生副作用。"""
from agent.procurement.models import TaskStatus, ApprovalStatus, ExecutionStatus


def after_authorize(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "parse"


def after_parse_request(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "validate_request"


def after_validate_request(state) -> str:
    if state.get("status") == TaskStatus.FAILED.value:
        return "fail"
    if state.get("status") == TaskStatus.NEED_INFO.value:
        return "ask_user"
    return "analyze"


def after_apply_clarification(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "validate_request"


def after_analyze(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "validate_plan"


def after_validate_plan(state) -> str:
    if state.get("validation_passed"):
        return "prepare_approval"
    if state.get("status") == TaskStatus.ANALYZING.value:
        return "analyze"
    return "fail"


def after_prepare_approval(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "request_approval"


def after_request_approval(state) -> str:
    return "fail" if state.get("status") == TaskStatus.FAILED.value else "finalize"


def after_finalize_approval(state) -> str:
    if state.get("status") == TaskStatus.FAILED.value:
        return "fail"
    if state.get("approval_status") == ApprovalStatus.REJECTED.value:
        return "reject"
    if state.get("approval_status") == ApprovalStatus.APPROVED.value:
        return "create_order"
    return "analyze"  # modify


def after_create_order(state) -> str:
    if state.get("status") == TaskStatus.APPROVED_PENDING_EXECUTION.value:
        return "pending"
    if state.get("execution_status") in {
        ExecutionStatus.SUCCEEDED.value,
        ExecutionStatus.UNKNOWN.value,
    }:
        return "verify"
    return "fail"


def after_verify_order(state) -> str:
    if state.get("execution_status") == ExecutionStatus.SUCCEEDED.value:
        return "complete"
    return "fail"
