from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agent.procurement.data_provider import UpstreamUnavailable
from agent.procurement.models import (
    ExecutionStatus,
    OrderExecutionResult,
    OrderVerificationResult,
    TaskRecord,
    ToolError,
)
from agent.procurement.task_repository import PostgresTaskRepository
from agent.procurement.task_runner import (
    ProcurementTaskRunner,
    TaskCheckpointMissing,
)
from agent.procurement.workflow import compile_procurement_graph
from tests.factories import make_candidates
from tests.workflow.support import (
    FakeOrderExecutor,
    StaticRequestParser,
    make_test_deps,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)


class CountingParser(StaticRequestParser):
    def __init__(self):
        super().__init__()
        self.parse_calls = 0

    async def parse(self, *args, **kwargs):
        self.parse_calls += 1
        return await super().parse(*args, **kwargs)


class FlakyDataProvider:
    def __init__(self):
        self.calls = 0

    async def collect(self, request):
        self.calls += 1
        if self.calls == 1:
            raise UpstreamUnavailable(
                "供应商查询超时", code="UPSTREAM_UNAVAILABLE"
            )
        return make_candidates()


class UnknownThenSuccessExecutor:
    def __init__(self):
        self.create_calls = 0
        self.verify_calls = 0

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        self.create_calls += 1
        return OrderExecutionResult(
            status=ExecutionStatus.UNKNOWN,
            error=ToolError(
                code="ORDER_RESULT_UNKNOWN",
                retryable=True,
                message="模拟订单请求超时",
            ),
        )

    async def verify_order(self, *, task_id, order_id):
        self.verify_calls += 1
        return OrderVerificationResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id="PO-TEST-UNKNOWN-001",
            order_number="PO20261022001",
        )


async def make_runner(deps, database, saver):
    repository = PostgresTaskRepository(database)
    await repository.setup()
    graph = compile_procurement_graph(deps, saver)
    return (
        ProcurementTaskRunner(
            graph=graph,
            task_repository=repository,
            now=lambda: NOW,
        ),
        repository,
    )


@pytest.mark.anyio
async def test_approval_interrupt_survives_restart(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()

    runner1, _ = await make_runner(deps, db, saver)
    started = await runner1.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    assert started.status == "awaiting_approval"
    assert started.interrupt["type"] == "procurement_approval"
    assert fake.create_calls == []

    # 模拟服务重启：新 graph + 新 runner，同一个 Checkpointer
    runner2, _ = await make_runner(deps, db, saver)
    resumed = await runner2.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )

    assert resumed.status == "completed"
    assert len(fake.create_calls) == 1


@pytest.mark.anyio
async def test_tool_timeout_enters_retry_gate_and_only_reruns_analyze(db):
    fake = FakeOrderExecutor(success=True)
    parser = CountingParser()
    provider = FlakyDataProvider()
    deps = make_test_deps(
        fake,
        database=db,
        parser=parser,
        data_provider=provider,
    )
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    assert started.status == "awaiting_retry"
    assert started.interrupt["type"] == "procurement_retry_required"
    assert provider.calls == 1

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"action": "retry"},
    )

    assert resumed.status == "awaiting_approval"
    assert resumed.interrupt["type"] == "procurement_approval"
    assert parser.parse_calls == 1  # 没有从 START 重跑
    assert provider.calls == 2


@pytest.mark.anyio
async def test_retry_cancel_stops_task(db):
    fake = FakeOrderExecutor(success=True)
    provider = FlakyDataProvider()
    deps = make_test_deps(fake, database=db, data_provider=provider)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    assert started.status == "awaiting_retry"

    cancelled = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"action": "cancel"},
    )

    assert cancelled.status == "cancelled"
    assert fake.create_calls == []


@pytest.mark.anyio
async def test_unknown_order_result_is_verified_not_recreated(db):
    executor = UnknownThenSuccessExecutor()
    deps = make_test_deps(executor, database=db)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    assert started.status == "awaiting_approval"

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )

    assert resumed.status == "completed"
    assert executor.create_calls == 1
    assert executor.verify_calls == 1


@pytest.mark.anyio
async def test_resume_with_different_user_is_forbidden(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )

    with pytest.raises(PermissionError):
        await runner.resume(
            task_id=started.task_id,
            user_id="u2",
            username="李四",
            resume_data={"decision": "approve"},
        )
    assert fake.create_calls == []


@pytest.mark.anyio
async def test_missing_checkpoint_marks_task_orphaned(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()
    runner, repository = await make_runner(deps, db, saver)

    await repository.create(
        TaskRecord(
            task_id="TASK-ORPHAN",
            thread_id="procurement:TASK-ORPHAN",
            user_id="u1",
            status="running",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    with pytest.raises(TaskCheckpointMissing):
        await runner.resume(
            task_id="TASK-ORPHAN",
            user_id="u1",
            username="张三",
            resume_data={"decision": "approve"},
        )

    record = await repository.get("TASK-ORPHAN")
    assert record.status == "orphaned"


@pytest.mark.anyio
async def test_terminal_resume_is_idempotent(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    completed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )
    assert completed.status == "completed"
    assert len(fake.create_calls) == 1

    again = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )
    assert again.status == "completed"
    assert len(fake.create_calls) == 1


class AlwaysUnknownExecutor:
    def __init__(self):
        self.create_calls = 0
        self.verify_calls = 0

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        self.create_calls += 1
        return OrderExecutionResult(
            status=ExecutionStatus.UNKNOWN,
            error=ToolError(
                code="ORDER_RESULT_UNKNOWN",
                retryable=True,
                message="模拟订单结果未知",
            ),
        )

    async def verify_order(self, *, task_id, order_id):
        self.verify_calls += 1
        return OrderVerificationResult(
            status=ExecutionStatus.UNKNOWN,
            order_id=order_id,
            error=ToolError(
                code="ORDER_RESULT_UNKNOWN",
                retryable=True,
                message="模拟核验仍未知",
            ),
        )


@pytest.mark.anyio
async def test_persistent_unknown_enters_retry_without_recreating(db):
    executor = AlwaysUnknownExecutor()
    deps = make_test_deps(executor, database=db)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    result = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )

    assert result.status == "awaiting_retry"
    assert result.interrupt["type"] == "procurement_retry_required"
    assert result.interrupt["retry_from"] == "verify_order"
    assert executor.create_calls == 1
    assert executor.verify_calls == 1


@pytest.mark.anyio
async def test_reconcile_marks_missing_checkpoint_orphaned(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()
    runner, repository = await make_runner(deps, db, saver)

    await repository.create(
        TaskRecord(
            task_id="TASK-RECONCILE",
            thread_id="procurement:TASK-RECONCILE",
            user_id="u1",
            status="running",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    checked = await runner.reconcile()
    assert checked == 1
    record = await repository.get("TASK-RECONCILE")
    assert record.status == "orphaned"


@pytest.mark.anyio
async def test_reconcile_keeps_awaiting_approval(db):
    fake = FakeOrderExecutor(success=True)
    deps = make_test_deps(fake, database=db)
    saver = MemorySaver()
    runner1, repository = await make_runner(deps, db, saver)

    started = await runner1.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    assert started.status == "awaiting_approval"

    runner2, _ = await make_runner(deps, db, saver)
    checked = await runner2.reconcile()
    assert checked == 1
    record = await repository.get(started.task_id)
    assert record.status == "awaiting_approval"
    assert record.current_node == "request_approval"


class TimeoutThenSuccessExecutor:
    def __init__(self):
        self.create_calls = 0
        self.verify_calls = 0

    async def create_order(self, *, task_id, plan, approval_id, idempotency_key):
        self.create_calls += 1
        raise TimeoutError("模拟订单接口超时")

    async def verify_order(self, *, task_id, order_id):
        self.verify_calls += 1
        return OrderVerificationResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id="PO-TEST-TIMEOUT-001",
            order_number="PO20261022002",
        )


@pytest.mark.anyio
async def test_create_timeout_is_verified_not_retried(db):
    executor = TimeoutThenSuccessExecutor()
    deps = make_test_deps(executor, database=db)
    saver = MemorySaver()
    runner, _ = await make_runner(deps, db, saver)

    started = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )
    result = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"decision": "approve"},
    )

    assert result.status == "completed"
    assert executor.create_calls == 1
    assert executor.verify_calls == 1
