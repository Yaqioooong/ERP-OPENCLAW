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
async def test_approval_is_required_before_order(tmp_path, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        db_path=tmp_path / "approval.db",
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


@pytest.mark.anyio
async def test_missing_warehouse_triggers_clarification(tmp_path, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        db_path=tmp_path / "approval.db",
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
async def test_reject_does_not_call_order_executor(tmp_path, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        db_path=tmp_path / "approval.db",
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
async def test_plan_change_after_interrupt_does_not_execute(tmp_path, config, ctx):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        db_path=tmp_path / "approval.db",
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
async def test_default_executor_stops_at_pending_execution(tmp_path, config, ctx):
    deps = make_test_deps(
        DisabledOrderExecutor(),
        db_path=tmp_path / "approval.db",
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
async def test_resume_with_different_user_is_forbidden(tmp_path, config):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(
        fake,
        db_path=tmp_path / "approval.db",
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