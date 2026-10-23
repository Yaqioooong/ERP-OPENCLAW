import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent.procurement.order_executor import DisabledOrderExecutor
from agent.procurement.workflow import compile_procurement_graph
from tests.factories import make_plan
from tests.workflow.support import (
    FakeOrderExecutor,
    StaticRequestParser,
    make_config,
    make_context,
    make_incomplete_request,
    make_state,
    make_test_deps,
)


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def ctx():
    return make_context()


@pytest.mark.anyio
async def test_approval_is_required_before_order(db, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    first = await graph.ainvoke(make_state(), config, context=ctx)
    assert first["__interrupt__"]
    assert fake.create_calls == []

    second = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=ctx,
    )
    assert len(fake.create_calls) == 1
    assert second["status"] == "completed"
    assert second["order_id"] == "PO-TEST-001"
    approval = await deps.approval_repository.get(second["approval_id"])
    assert approval is not None
    assert approval.approved_by == "u1"
    assert approval.plan_snapshot is not None
    assert approval.approved_by_roles == ["requester"]


@pytest.mark.anyio
async def test_missing_warehouse_triggers_clarification(db, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        parser=StaticRequestParser(make_incomplete_request()),
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    first = await graph.ainvoke(make_state(), config, context=ctx)
    interrupt_value = first["__interrupt__"][0].value
    assert interrupt_value["type"] == "procurement_clarification"
    assert "warehouse_location" in interrupt_value["missing_fields"]

    second = await graph.ainvoke(
        Command(resume={"request_patch": {"warehouse_location": "华东仓"}}),
        config,
        context=ctx,
    )
    assert second["__interrupt__"][0].value["type"] == "procurement_approval"


@pytest.mark.anyio
async def test_reject_does_not_call_order_executor(db, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    await graph.ainvoke(make_state(), config, context=ctx)
    result = await graph.ainvoke(
        Command(resume={"decision": "reject"}),
        config,
        context=ctx,
    )
    assert result["status"] == "rejected"
    assert fake.create_calls == []


@pytest.mark.anyio
async def test_plan_change_after_interrupt_does_not_execute(db, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    await graph.ainvoke(make_state(), config, context=ctx)

    # 模拟外部把方案内容改掉，但保留旧的 plan_hash/approval_id
    changed = make_plan(quantity=300)
    await graph.aupdate_state(
        config,
        {"procurement_plan": changed.model_dump(mode="json")},
    )

    result = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=ctx,
    )
    assert result["status"] == "failed"
    assert result["last_error"]["code"] in {
        "PLAN_STATE_INCONSISTENT",
        "APPROVAL_STALE",
    }
    assert fake.create_calls == []


@pytest.mark.anyio
async def test_default_executor_stops_at_pending_execution(db, config, ctx):
    deps = make_test_deps(
        DisabledOrderExecutor(),
        database=db,
        execution_enabled=False,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    await graph.ainvoke(make_state(), config, context=ctx)
    result = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=ctx,
    )
    assert result["status"] == "approved_pending_execution"
    assert result["execution_status"] == "not_started"


@pytest.mark.anyio
async def test_resume_with_different_user_is_forbidden(db, config):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    owner = make_context("u1")
    intruder = make_context("u2")

    await graph.ainvoke(make_state(), config, context=owner)
    result = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=intruder,
    )
    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "FORBIDDEN"
    assert fake.create_calls == []

@pytest.mark.anyio
async def test_approver_role_can_approve_other_users_task(db, config):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    owner = make_context("u1")
    approver = make_context("u2", roles=("approver",))

    await graph.ainvoke(make_state(), config, context=owner)
    result = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=approver,
    )

    assert result["status"] == "completed"
    assert result["approved_by"] == "u2"
    assert len(fake.create_calls) == 1
    approval = await deps.approval_repository.get(result["approval_id"])
    assert approval is not None
    assert approval.approved_by == "u2"
    assert approval.approved_by_roles == ["approver"]


@pytest.mark.anyio
async def test_distinct_approver_policy_rejects_self_approval(db, config):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        database=db,
        execution_enabled=True,
        require_distinct_approver=True,
    )
    graph = compile_procurement_graph(deps, MemorySaver())

    ctx = make_context("u1")
    await graph.ainvoke(make_state(), config, context=ctx)
    result = await graph.ainvoke(
        Command(resume={"decision": "approve"}),
        config,
        context=ctx,
    )

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "FORBIDDEN"
    assert fake.create_calls == []
