from datetime import datetime
from zoneinfo import ZoneInfo

from agent.procurement.task_events import TaskEvent, TaskEventType, encode_sse

TZ = ZoneInfo("Asia/Shanghai")


def test_encode_sse_has_id_event_and_data():
    event = TaskEvent(
        event_id="evt-1",
        task_id="TASK-1",
        sequence=3,
        type=TaskEventType.APPROVAL_REQUIRED,
        timestamp=datetime(2026, 10, 23, 10, 0, tzinfo=TZ),
        data={"approval_id": "APR-1"},
    )

    text = encode_sse(event)

    assert "id: 3\n" in text
    assert "event: approval.required\n" in text
    assert '"event_id": "evt-1"' in text
    assert text.endswith("\n\n")


def test_heartbeat_does_not_emit_id():
    event = TaskEvent(
        event_id="evt-hb",
        task_id="TASK-1",
        sequence=3,
        type=TaskEventType.TASK_HEARTBEAT,
        timestamp=datetime(2026, 10, 23, 10, 0, tzinfo=TZ),
        data={},
    )

    text = encode_sse(event)

    assert "id:" not in text
    assert "event: task.heartbeat\n" in text
