from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.task_events import (
    InMemoryTaskEventBroker,
    PostgresTaskEventStore,
    TaskEventService,
    TaskEventType,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


def make_service(db):
    return TaskEventService(
        store=PostgresTaskEventStore(db),
        broker=InMemoryTaskEventBroker(),
    )


@pytest.mark.anyio
async def test_event_sequence_is_monotonic(db):
    service = make_service(db)

    first = await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.TASK_STARTED,
        data={"status": "running"},
        timestamp=NOW,
    )
    second = await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.TASK_PROGRESS,
        data={"status": "analyzing"},
        timestamp=NOW,
    )

    assert first.sequence == 1
    assert second.sequence == 2
    events = await service._store.list_after("TASK-1", after_sequence=1)
    assert [event.sequence for event in events] == [2]


@pytest.mark.anyio
async def test_event_sanitizes_sensitive_fields(db):
    service = make_service(db)

    event = await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.TASK_PROGRESS,
        data={
            "status": "running",
            "access_token": "secret",
            "nested": {"password": "x", "safe": "ok"},
        },
        timestamp=NOW,
    )

    assert "access_token" not in event.data
    assert "password" not in event.data["nested"]
    assert event.data["nested"]["safe"] == "ok"


@pytest.mark.anyio
async def test_stream_replays_to_terminal(db):
    service = make_service(db)
    await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.TASK_STARTED,
        data={"status": "running"},
        timestamp=NOW,
    )
    await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.APPROVAL_REQUIRED,
        data={"approval_id": "APR-1"},
        timestamp=NOW,
    )
    await service.emit(
        task_id="TASK-1",
        event_type=TaskEventType.TASK_COMPLETED,
        data={"status": "completed"},
        timestamp=NOW,
    )

    events = [
        event
        async for event in service.stream(task_id="TASK-1", after_sequence=0)
    ]

    assert [event.type for event in events] == [
        TaskEventType.TASK_STARTED,
        TaskEventType.APPROVAL_REQUIRED,
        TaskEventType.TASK_COMPLETED,
    ]


@pytest.mark.anyio
async def test_task_runner_emits_lifecycle_events(db):
    from langgraph.checkpoint.memory import MemorySaver

    from agent.procurement.task_repository import PostgresTaskRepository
    from agent.procurement.task_runner import ProcurementTaskRunner
    from agent.procurement.workflow import compile_procurement_graph
    from tests.workflow.support import FakeOrderExecutor, make_test_deps

    class FakeEvents:
        def __init__(self):
            self.events = []

        async def emit(self, *, task_id, event_type, data=None, timestamp=None):
            self.events.append(event_type)

    events = FakeEvents()
    deps = make_test_deps(
        FakeOrderExecutor(success=True), database=db
    )
    graph = compile_procurement_graph(deps, MemorySaver())
    repository = PostgresTaskRepository(db)
    await repository.setup()
    runner = ProcurementTaskRunner(
        graph=graph,
        task_repository=repository,
        now=lambda: NOW,
        event_service=events,
    )

    result = await runner.start(
        raw_request="为华东仓补充 200 个活塞环组件",
        user_id="u1",
        username="张三",
    )

    assert result.status == "awaiting_approval"
    assert TaskEventType.TASK_STARTED in events.events
    assert TaskEventType.APPROVAL_REQUIRED in events.events
