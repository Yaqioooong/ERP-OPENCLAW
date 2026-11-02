from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from loguru import logger as loguru_logger

from agent.procurement.models import ApprovalDecisionResult
from agent.procurement.observability import trace_node
from agent.procurement.workflow import compile_procurement_graph
from tests.workflow.support import (
    FakeOrderExecutor,
    make_config,
    make_context,
    make_state,
    make_test_deps,
)


class MemoryApprovalRepository:
    def __init__(self):
        self.records = {}

    async def ensure_pending(self, record):
        self.records[record.approval_id] = record

    async def get(self, approval_id):
        return self.records.get(approval_id)

    async def decide(
            self,
            *,
            approval_id,
            task_id,
            plan_version,
            plan_hash,
            status,
            decided_by,
            decision,
            comment,
            decided_by_roles=None,
    ):
        record = self.records.get(approval_id)
        if record is None:
            return ApprovalDecisionResult.STALE
        if record.status.value != "pending":
            if (
                    record.status.value == status
                    and record.decision == decision
                    and record.plan_version == plan_version
                    and record.plan_hash == plan_hash
            ):
                return ApprovalDecisionResult.ALREADY_APPLIED
            return ApprovalDecisionResult.STALE
        self.records[approval_id] = record.model_copy(
            update={
                "status": status,
                "approved_by": decided_by,
                "decision": decision,
                "comment": comment,
                "approved_by_roles": decided_by_roles or [],
            }
        )
        return ApprovalDecisionResult.APPLIED


def capture_logs():
    messages = []
    sink_id = loguru_logger.add(
        lambda message: messages.append(str(message)),
        level="INFO",
    )
    return messages, sink_id


@pytest.mark.anyio
async def test_successful_order_chain_is_traceable_by_task_id():
    approval_repository = MemoryApprovalRepository()
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=None,
        execution_enabled=True,
    )
    deps = replace(deps, approval_repository=approval_repository)
    graph = compile_procurement_graph(deps, MemorySaver())
    task_id = "TASK-OBS-SUCCESS"
    config = make_config(task_id)
    context = make_context()
    messages, sink_id = capture_logs()

    try:
        first = await graph.ainvoke(
            make_state(task_id=task_id),
            config,
            context=context,
        )
        assert first["__interrupt__"]
        result = await graph.ainvoke(
            Command(resume={"decision": "approve"}),
            config,
            context=context,
        )
    finally:
        loguru_logger.remove(sink_id)

    assert result["status"] == "completed"
    logs = "\n".join(messages)
    assert f"task_id={task_id}" in logs
    for node in (
            "authorize",
            "parse_request",
            "validate_request",
            "analyze_procurement",
            "validate_plan",
            "prepare_approval",
            "request_approval",
            "finalize_approval",
            "create_order",
            "verify_order",
            "complete_task",
    ):
        assert f"procurement.node.start task_id={task_id} node={node}" in logs

    assert (
        f"procurement.node.interrupted task_id={task_id} "
        "node=request_approval"
    ) in logs
    assert (
        f"procurement.node.route task_id={task_id} "
        "from=create_order route=verify to=verify_order"
    ) in logs
    assert (
        f"procurement.node.route task_id={task_id} "
        "from=verify_order route=complete to=complete_task"
    ) in logs
    assert (
        f"procurement.node.result task_id={task_id} "
        "node=verify_order"
    ) in logs
    assert "order_id=PO-TEST-001" in logs
    assert f"procurement.order.create.start task_id={task_id}" in logs
    assert f"procurement.order.verify.start task_id={task_id}" in logs
    assert f"procurement.plan.validation task_id={task_id} passed=True" in logs
    assert "task_id=%s" not in logs


@pytest.mark.anyio
async def test_business_failure_logs_error_code_and_truncates_message():
    async def failing_node(state, runtime):
        return {
            "status": "failed",
            "last_error": {
                "code": "ORDER_CREATE_FAILED",
                "message": "first line\n" + "x" * 800,
            },
        }

    wrapped = trace_node("create_order", failing_node)
    messages, sink_id = capture_logs()
    try:
        result = await wrapped(
            {"task_id": "TASK-OBS-FAILED"},
            SimpleNamespace(context=None),
        )
    finally:
        loguru_logger.remove(sink_id)

    assert result["last_error"]["code"] == "ORDER_CREATE_FAILED"
    logs = "\n".join(messages)
    error_line = next(
        line
        for line in messages
        if "procurement.node.error" in line
    )
    assert "task_id=TASK-OBS-FAILED" in error_line
    assert "node=create_order" in error_line
    assert "status=failed" in error_line
    assert "error_code=ORDER_CREATE_FAILED" in error_line
    assert "first line" in logs
    assert "\n" + "x" * 600 not in logs


@pytest.mark.anyio
async def test_create_order_failure_routes_to_fail_task_with_error_code():
    approval_repository = MemoryApprovalRepository()
    deps = make_test_deps(
        FakeOrderExecutor(success=False),
        database=None,
        execution_enabled=True,
    )
    deps = replace(deps, approval_repository=approval_repository)
    graph = compile_procurement_graph(deps, MemorySaver())
    task_id = "TASK-OBS-CREATE-FAILED"
    config = make_config(task_id)
    context = make_context()

    await graph.ainvoke(
        make_state(task_id=task_id),
        config,
        context=context,
    )
    messages, sink_id = capture_logs()
    try:
        result = await graph.ainvoke(
            Command(resume={"decision": "approve"}),
            config,
            context=context,
        )
    finally:
        loguru_logger.remove(sink_id)

    assert result["status"] == "failed"
    logs = "\n".join(messages)
    assert (
        f"procurement.node.route task_id={task_id} "
        "from=create_order route=fail to=fail_task"
    ) in logs
    create_error = next(
        line
        for line in messages
        if (
            "procurement.node.error" in line
            and "node=create_order" in line
        )
    )
    assert "error_code=FAKE_FAILED" in create_error
    assert "node=fail_task" in logs


@pytest.mark.anyio
async def test_exception_log_keeps_exception_semantics():
    async def exploding_node(state, runtime):
        raise ValueError("broken upstream payload")

    wrapped = trace_node("analyze_procurement", exploding_node)
    messages, sink_id = capture_logs()
    try:
        with pytest.raises(ValueError, match="broken upstream payload"):
            await wrapped(
                {"task_id": "TASK-OBS-EXCEPTION"},
                SimpleNamespace(context=None),
            )
    finally:
        loguru_logger.remove(sink_id)

    logs = "\n".join(messages)
    assert "procurement.node.error task_id=TASK-OBS-EXCEPTION" in logs
    assert "node=analyze_procurement" in logs
    assert "error_code=ValueError" in logs


@pytest.mark.anyio
async def test_interrupt_log_keeps_interrupt_semantics():
    async def interrupting_node(state, runtime):
        raise GraphInterrupt()

    wrapped = trace_node("ask_user", interrupting_node)
    messages, sink_id = capture_logs()
    try:
        with pytest.raises(GraphInterrupt):
            await wrapped(
                {"task_id": "TASK-OBS-INTERRUPT", "status": "new"},
                SimpleNamespace(context=None),
            )
    finally:
        loguru_logger.remove(sink_id)

    logs = "\n".join(messages)
    assert (
        "procurement.node.interrupted task_id=TASK-OBS-INTERRUPT "
        "node=ask_user status=new"
    ) in logs


@pytest.mark.anyio
async def test_node_logs_do_not_include_request_or_full_state_payloads():
    sensitive_preference = "只采购内部机密供应商 A"

    async def echo_state_node(state, runtime):
        return {"status": "parsing"}

    wrapped = trace_node("parse_request", echo_state_node)
    state = {
        "task_id": "TASK-OBS-REDACTION",
        "status": "new",
        "raw_request": sensitive_preference,
        "request": {"description": sensitive_preference},
        "supplier_candidates": [{"payload": "x" * 1000}],
        "procurement_plan": {"rationale": "x" * 1000},
    }
    messages, sink_id = capture_logs()
    try:
        await wrapped(state, SimpleNamespace(context=None))
    finally:
        loguru_logger.remove(sink_id)

    logs = "\n".join(messages)
    assert sensitive_preference not in logs
    assert "x" * 100 not in logs
    assert "procurement.node.start" in logs
    assert "procurement.node.result" in logs
