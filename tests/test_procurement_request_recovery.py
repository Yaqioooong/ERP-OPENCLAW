from datetime import datetime
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agent.procurement.data_provider import UpstreamUnavailable
from agent.procurement.models import ProcurementRequest, RequestedItem
from agent.procurement.ports import (
    PartResolution,
    PartResolutionError,
    SupplierResolution,
    SupplierResolutionError,
)
from agent.procurement.request_normalization import ProcurementRequestInputError
from agent.procurement.task_repository import PostgresTaskRepository
from agent.procurement.task_runner import ProcurementTaskRunner
from agent.procurement.workflow import compile_procurement_graph
from tests.workflow.support import (
    FakeOrderExecutor,
    StaticDataProvider,
    StaticPlanProposer,
    make_test_deps,
)
from tests.factories import make_candidates

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)


class StaticSupplierResolver:
    def __init__(self):
        self.calls = []

    async def resolve(
        self,
        *,
        supplier_id: int | None,
        supplier_name: str | None,
    ) -> SupplierResolution:
        self.calls.append((supplier_id, supplier_name))
        if supplier_id == 31:
            return SupplierResolution(id=31, name="电装配件专营店", status=1)
        if supplier_id is not None:
            raise SupplierResolutionError(
                code="SUPPLIER_NOT_FOUND",
                message=f"供应商 {supplier_id} 不存在。",
            )
        if supplier_name == "电装配件专营店":
            return SupplierResolution(id=31, name=supplier_name, status=1)
        raise SupplierResolutionError(
            code="SUPPLIER_AMBIGUOUS",
            message=(
                "供应商“DENSO”匹配到多个主体，请填写供应商 ID："
                "31-电装配件专营店；2-电装（上海）国际贸易有限公司。"
            ),
            candidates=[
                {"id": 31, "name": "电装配件专营店"},
                {"id": 2, "name": "电装（上海）国际贸易有限公司"},
            ],
        )


async def make_runner(deps, database, saver):
    repository = PostgresTaskRepository(database)
    await repository.setup()
    graph = compile_procurement_graph(deps, saver)
    runner = ProcurementTaskRunner(
        graph=graph,
        task_repository=repository,
        now=lambda: NOW,
    )
    return runner, repository


class MemoryTaskRepository:
    """无需外部数据库的任务仓储，覆盖恢复状态机的确定性行为。"""

    def __init__(self):
        self.records = {}

    async def setup(self):
        return None

    async def create(self, record):
        self.records[record.task_id] = record

    async def get(self, task_id):
        return self.records.get(task_id)

    async def get_by_thread(self, thread_id):
        return next(
            (
                record
                for record in self.records.values()
                if record.thread_id == thread_id
            ),
            None,
        )

    async def update_status(
        self,
        *,
        task_id,
        status,
        current_node=None,
        retry_from=None,
        error=None,
        now=None,
    ):
        current = self.records[task_id]
        if current.status in {"completed", "rejected", "failed", "cancelled", "orphaned"}:
            return
        self.records[task_id] = current.model_copy(
            update={
                "status": status,
                "current_node": current_node or current.current_node,
                "retry_from": retry_from,
                "last_error_code": error.get("code") if error else None,
                "last_error_message": error.get("message") if error else None,
                "updated_at": now or current.updated_at,
            }
        )

    async def mark_resumed(self, task_id, now):
        current = self.records[task_id]
        self.records[task_id] = current.model_copy(
            update={"resumed_at": now, "updated_at": now}
        )

    async def list_recoverable(self, **_kwargs):
        return []

    async def list_by_user(self, user_id, **_kwargs):
        return [
            record
            for record in self.records.values()
            if record.user_id == user_id
        ]

    async def list_by_conversation(
        self, *, user_id, conversation_id, limit=50, offset=0, **_kwargs
    ):
        matches = [
            record
            for record in self.records.values()
            if record.user_id == user_id
            and record.conversation_id == conversation_id
        ]
        matches.sort(key=lambda record: record.updated_at, reverse=True)
        return matches[offset : offset + limit]


class MemoryApprovalRepository:
    def __init__(self):
        self.records = {}

    async def ensure_pending(self, record):
        self.records.setdefault(record.approval_id, record)

    async def get(self, approval_id):
        return self.records.get(approval_id)


async def make_memory_runner(deps):
    repository = MemoryTaskRepository()
    deps = replace(deps, approval_repository=MemoryApprovalRepository())
    runner = ProcurementTaskRunner(
        graph=compile_procurement_graph(deps, MemorySaver()),
        task_repository=repository,
        now=lambda: NOW,
    )
    return runner, repository


def incomplete_request() -> ProcurementRequest:
    return ProcurementRequest(
        items=[
            RequestedItem(
                part_name="电装 DENSO U27ETR",
                quantity=10,
            )
        ]
    )


class ScriptedPartDataProvider(StaticDataProvider):
    """按顺序返回物料解析结果，覆盖无需数据库的恢复状态机。"""

    def __init__(self, outcomes):
        super().__init__(make_candidates(supplier_id=31))
        self.outcomes = list(outcomes)
        self.calls = []

    async def resolve_part(self, item):
        self.calls.append(item)
        if not self.outcomes:
            raise AssertionError("unexpected part resolution call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.anyio
async def test_in_memory_recovery_flow_and_preflight_guard():
    resolver = StaticSupplierResolver()
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=None,
        data_provider=StaticDataProvider(make_candidates(supplier_id=31)),
        proposer=StaticPlanProposer(supplier_id=31),
        supplier_resolver=resolver,
    )
    runner, repository = await make_memory_runner(deps)

    invalid_count = len(repository.records)
    with pytest.raises(ProcurementRequestInputError):
        await runner.start(
            raw_request="",
            user_id="u1",
            username="张三",
            request={
                "items": [{"part_name": "火花塞", "quantity": "不是数量"}],
                "warehouse_location": "华东仓",
            },
        )
    assert len(repository.records) == invalid_count

    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request={
            "items": [
                {
                    "part_id": 1,
                    "part_name": "电装 DENSO U27ETR",
                    "quantity": 10,
                }
            ],
            "preferred_supplier_id": "DENSO",
        },
    )
    assert started.status == "need_info"
    assert "warehouse_location" in started.interrupt["missing_fields"]

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={
            "request_patch": {
                "warehouse_location": "华东仓",
                "required_by": "2026-11-10",
                "budget": None,
            }
        },
    )
    assert resumed.status == "need_info"
    assert "preferred_supplier_id" in resumed.interrupt["missing_fields"]

    approved = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"request_patch": {"preferred_supplier_id": 31}},
    )
    assert approved.status == "awaiting_approval"
    duplicate = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"request_patch": {"preferred_supplier_id": 31}},
    )
    assert duplicate.status == "awaiting_approval"

    await repository.update_status(
        task_id=started.task_id,
        status="failed",
        error={"code": "TASK_START_FAILED", "message": "模拟后台失败"},
        now=NOW,
    )
    failed = await runner.get(
        task_id=started.task_id,
        user_id="u1",
        roles=("requester",),
    )
    assert failed.status == "failed"
    assert failed.state_summary["last_error"]["code"] == "TASK_START_FAILED"
    assert failed.recoverable is False


@pytest.mark.anyio
async def test_need_info_resume_preserves_task_and_is_idempotent(db):
    resolver = StaticSupplierResolver()
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=db,
        supplier_resolver=resolver,
    )
    runner, _ = await make_runner(deps, db, MemorySaver())

    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request=incomplete_request().model_dump(mode="json"),
    )

    assert started.status == "need_info"
    assert started.interrupt["type"] == "procurement_clarification"
    assert "warehouse_location" in started.interrupt["missing_fields"]

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={
            "request_patch": {
                "warehouse_location": "华东仓",
                "required_by": "2026-11-10",
                "budget": None,
            }
        },
    )

    assert resumed.task_id == started.task_id
    assert resumed.status == "awaiting_approval"

    duplicate = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={
            "request_patch": {
                "warehouse_location": "华东仓",
                "required_by": "2026-11-10",
                "budget": None,
            }
        },
    )

    assert duplicate.task_id == started.task_id
    assert duplicate.status == "awaiting_approval"


@pytest.mark.anyio
async def test_ambiguous_supplier_enters_need_info_then_resolves_by_id(db):
    resolver = StaticSupplierResolver()
    proposer = StaticPlanProposer(supplier_id=31)
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=db,
        data_provider=StaticDataProvider(make_candidates(supplier_id=31)),
        proposer=proposer,
        supplier_resolver=resolver,
    )
    runner, _ = await make_runner(deps, db, MemorySaver())

    request = ProcurementRequest(
        items=[RequestedItem(part_name="电装 DENSO U27ETR", quantity=10)],
        warehouse_location="华东仓",
        preferred_supplier_name="DENSO",
    )
    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request=request.model_dump(mode="json"),
    )

    assert started.status == "need_info"
    assert started.interrupt["current_request"]["preferred_supplier_name"] == "DENSO"
    assert "preferred_supplier_id" in started.interrupt["missing_fields"]
    assert "多个主体" in started.interrupt["validation_errors"][0]["message"]

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"request_patch": {"preferred_supplier_id": 31}},
    )

    assert resumed.status == "awaiting_approval"
    assert resolver.calls[-1] == (31, "DENSO")


@pytest.mark.anyio
async def test_unique_part_name_is_backfilled_before_planning():
    provider = ScriptedPartDataProvider(
        [PartResolution(id=1, name="电装 DENSO U27ETR", code="U27ETR")]
    )
    proposer = StaticPlanProposer(supplier_id=31)
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=None,
        data_provider=provider,
        proposer=proposer,
    )
    runner, _ = await make_memory_runner(deps)

    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request={
            "items": [
                {"part_name": "电装 DENSO U27ETR", "quantity": 10}
            ],
            "warehouse_location": "华东仓",
        },
    )

    assert started.status == "awaiting_approval"
    assert provider.calls[0].part_id is None
    assert proposer.requests[0].items[0].part_id == 1
    assert proposer.requests[0].items[0].part_name == "电装 DENSO U27ETR"


@pytest.mark.anyio
async def test_ambiguous_part_and_warehouse_are_reported_together_then_resumed():
    provider = ScriptedPartDataProvider(
        [
            PartResolutionError(
                code="PART_AMBIGUOUS",
                message=(
                    "物料“电装 DENSO U27ETR”匹配到多个记录，"
                    "请填写物料 ID：1-电装 DENSO U27ETR（A）；"
                    "2-电装 DENSO U27ETR（B）。"
                ),
            ),
            PartResolution(id=1, name="电装 DENSO U27ETR", code="U27ETR-A"),
        ]
    )
    proposer = StaticPlanProposer(supplier_id=31)
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=None,
        data_provider=provider,
        proposer=proposer,
    )
    runner, _ = await make_memory_runner(deps)

    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request={
            "items": [
                {"part_name": "电装 DENSO U27ETR", "quantity": 10}
            ]
        },
    )

    assert started.status == "need_info"
    assert set(started.interrupt["missing_fields"]) == {
        "items[0].part_id",
        "warehouse_location",
    }
    assert len(started.interrupt["validation_errors"]) == 2
    assert started.interrupt["current_request"]["items"][0]["part_name"] == (
        "电装 DENSO U27ETR"
    )

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={
            "request_patch": {
                "warehouse_location": "华东仓",
                "items": [
                    {
                        "part_id": 1,
                        "part_name": "电装 DENSO U27ETR",
                        "quantity": 10,
                    }
                ],
            }
        },
    )

    assert resumed.task_id == started.task_id
    assert resumed.status == "awaiting_approval"
    assert proposer.requests[0].items[0].part_id == 1


@pytest.mark.anyio
async def test_part_resolution_upstream_failure_enters_retry_and_can_continue():
    provider = ScriptedPartDataProvider(
        [
            UpstreamUnavailable("物料目录暂时不可用", code="UPSTREAM_TIMEOUT"),
            PartResolution(id=1, name="电装 DENSO U27ETR", code="U27ETR"),
        ]
    )
    deps = make_test_deps(
        FakeOrderExecutor(success=True),
        database=None,
        data_provider=provider,
        proposer=StaticPlanProposer(supplier_id=31),
    )
    runner, _ = await make_memory_runner(deps)

    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request={
            "items": [
                {"part_name": "电装 DENSO U27ETR", "quantity": 10}
            ],
            "warehouse_location": "华东仓",
        },
    )

    assert started.status == "awaiting_retry"
    assert started.interrupt["retry_from"] == "validate_request"
    assert started.interrupt["last_error"]["retryable"] is True

    resumed = await runner.resume(
        task_id=started.task_id,
        user_id="u1",
        username="张三",
        resume_data={"action": "retry"},
    )

    assert resumed.task_id == started.task_id
    assert resumed.status == "awaiting_approval"


@pytest.mark.anyio
async def test_preflight_rejects_invalid_request_before_task_creation(db):
    deps = make_test_deps(FakeOrderExecutor(success=True), database=db)
    runner, _ = await make_runner(deps, db, MemorySaver())

    with pytest.raises(ProcurementRequestInputError) as exc_info:
        await runner.start(
            raw_request="",
            user_id="u1",
            username="张三",
            request={
                "items": [{"part_name": "火花塞", "quantity": "不是数量"}],
                "warehouse_location": "华东仓",
            },
        )

    assert exc_info.value.errors[0]["field"] == "items[0].quantity"


@pytest.mark.anyio
async def test_terminal_failure_is_not_overwritten_by_checkpoint_snapshot(db):
    deps = make_test_deps(FakeOrderExecutor(success=True), database=db)
    runner, repository = await make_runner(deps, db, MemorySaver())
    started = await runner.start(
        raw_request="",
        user_id="u1",
        username="张三",
        request=incomplete_request().model_dump(mode="json"),
    )
    assert started.status == "need_info"

    await repository.update_status(
        task_id=started.task_id,
        status="failed",
        error={
            "code": "TASK_START_FAILED",
            "message": "preferred_supplier_id 类型错误",
        },
        now=NOW,
    )

    current = await runner.get(
        task_id=started.task_id,
        user_id="u1",
        roles=("requester",),
    )

    assert current.status == "failed"
    assert current.state_summary["last_error"]["code"] == "TASK_START_FAILED"
    assert current.recoverable is False
