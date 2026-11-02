from datetime import date

from loguru import logger as loguru_logger

from agent.procurement.models import ProcurementRequest, RequestedItem
from agent.procurement.rules import (
    compute_plan_hash,
    execution_guard_errors,
    recompute_plan_amounts,
    validate_plan,
    validate_request_fields,
)
from tests.factories import make_plan, make_candidates, make_request


def test_request_missing_warehouse_is_fixable():
    issues = validate_request_fields(
        ProcurementRequest(items=[RequestedItem(part_name="活塞环", quantity=200)]),
        today=date(2026, 10, 21),
    )
    assert any(i.code == "REQUEST_WAREHOUSE_MISSING" and i.fixable for i in issues)


def test_request_rejects_negative_quantity():
    issues = validate_request_fields(
        ProcurementRequest(
            items=[RequestedItem(part_name="活塞环", quantity=-1)],
            warehouse_location="华东仓",
        ),
        today=date(2026, 10, 21),
    )
    assert any(i.code == "REQUEST_QUANTITY_INVALID" for i in issues)


def test_plan_hash_changes_when_quantity_changes():
    p1 = make_plan(quantity=200)
    p2 = make_plan(quantity=201)
    assert compute_plan_hash(p1) != compute_plan_hash(p2)


def test_plan_hash_ignores_rationale():
    p1 = make_plan(rationale="价格优先")
    p2 = make_plan(rationale="交期优先")
    assert compute_plan_hash(p1) == compute_plan_hash(p2)


def test_plan_total_mismatch_is_detected():
    plan = make_plan(subtotal="100.00", total="999.00")
    total, issues = recompute_plan_amounts(plan)
    assert any(i.code == "PLAN_TOTAL_MISMATCH" for i in issues)


def test_plan_rejects_price_not_in_candidates():
    plan = make_plan(unit_price="1.00")
    candidates = make_candidates(unit_price="100.00")
    request = make_request()
    result = validate_plan(
        plan,
        request,
        candidates,
        task_id="TASK-1",
        today=date(2026, 10, 21),
    )
    assert not result.passed
    assert any(e.code == "PLAN_PRICE_MISMATCH" for e in result.errors)


def test_unknown_delivery_is_not_retried_without_new_information():
    plan = make_plan(expected_delivery_date=None)
    candidates = make_candidates(lead_time_days=None)
    request = make_request(required_by=date(2026, 11, 10))

    result = validate_plan(
        plan,
        request,
        candidates,
        task_id="TASK-1",
        today=date(2026, 11, 2),
    )

    error = next(e for e in result.errors if e.code == "PLAN_DELIVERY_UNKNOWN")
    assert error.retryable is False
    assert "活塞环组件" in error.message


def test_validate_plan_logs_each_check_and_failure_detail():
    plan = make_plan(unit_price="1.00")
    candidates = make_candidates(unit_price="100.00")
    request = make_request()
    messages = []
    sink_id = loguru_logger.add(
        lambda message: messages.append(str(message)),
        level="INFO",
    )
    try:
        result = validate_plan(
            plan,
            request,
            candidates,
            task_id="TASK-1",
            today=date(2026, 10, 21),
        )
    finally:
        loguru_logger.remove(sink_id)

    assert not result.passed
    logs = "\n".join(messages)
    steps = {
        "task_identity",
        "warehouse_consistency",
        "supplier_candidate",
        "supplier_status",
        "preferred_supplier",
        "item_count",
        "requested_parts",
        "candidate_prices",
        "amount_recompute",
        "budget",
        "delivery",
        "inventory",
    }
    for step in steps:
        assert (
            f"procurement.plan.check.start task_id=TASK-1 step={step}"
            in logs
        )
        assert (
            f"procurement.plan.check.result task_id=TASK-1 step={step}"
            in logs
        )

    price_line = next(
        line
        for line in messages
        if (
            "procurement.plan.check.result" in line
            and "step=candidate_prices" in line
        )
    )
    assert "outcome=failed" in price_line
    assert "error_codes=PLAN_PRICE_MISMATCH" in price_line
    assert "error_fields=items[0].unit_price" in price_line
    assert "error_retryable=PLAN_PRICE_MISMATCH:true" in price_line
    assert "第 1 条明细单价应为 100.00，方案给出 1.00" in price_line

def test_execution_guard_detects_plan_hash_tampering():
    plan = make_plan(quantity=200)
    plan_hash = compute_plan_hash(plan)
    state = {
        "approval_status": "approved",
        "procurement_plan": plan.model_dump(mode="json"),
        "plan_version": 1,
        "plan_hash": plan_hash,
        "approval_plan_version": 1,
        "approval_plan_hash": plan_hash,
        "approved_plan_version": 1,
        "approved_plan_hash": plan_hash,
    }

    changed = make_plan(quantity=300)
    state["procurement_plan"] = changed.model_dump(mode="json")

    errors = execution_guard_errors(state)
    assert "PLAN_HASH_INCONSISTENT" in errors
    assert "APPROVAL_PLAN_HASH_STALE" in errors
    assert "APPROVED_PLAN_HASH_STALE" in errors


def test_execution_guard_rejects_expired_or_missing_approver():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    plan = make_plan()
    plan_hash = compute_plan_hash(plan)
    state = {
        "approval_status": "approved",
        "approved_by": None,
        "approval_expires_at": "2026-10-21T09:00:00+08:00",
        "procurement_plan": plan.model_dump(mode="json"),
        "plan_version": 1,
        "plan_hash": plan_hash,
        "approval_plan_version": 1,
        "approval_plan_hash": plan_hash,
        "approved_plan_version": 1,
        "approved_plan_hash": plan_hash,
    }
    now = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    errors = execution_guard_errors(state, now=now)
    assert "APPROVAL_APPROVER_MISSING" in errors
    assert "APPROVAL_EXPIRED" in errors
