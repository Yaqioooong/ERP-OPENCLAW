import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

import agent.tools.procurement_task_tools as task_tools
from agent.tools.procurement_task_tools import (
    _normalize_request,
    _restore_confirmed_part_identity,
    build_procurement_task_tools,
)
from agent.procurement.request_normalization import (
    ProcurementRequestInputError,
    normalize_procurement_request,
    validate_procurement_request,
)
from agent.schema import ProcurementContext


def test_normalize_material_aliases():
    request = _normalize_request(
        {
            "items": [
                {
                    "material_id": 67,
                    "material_name": "火花塞",
                    "quantity": 1,
                }
            ],
            "warehouse_location": "C区-3号库",
            "budget": 100,
        }
    )

    item = request["items"][0]
    assert item["part_id"] == 67
    assert item["part_name"] == "火花塞"
    assert item["quantity"] == 1
    assert "material_id" not in item
    assert "material_name" not in item


def test_restores_confirmed_part_model_after_confirmation():
    request = _normalize_request(
        {
            "items": [{"part_name": "火花塞", "quantity": 10}],
            "warehouse_location": "C区-3号库",
            "required_by": "2026-11-10",
            "preferred_supplier_name": "DENSO",
        }
    )
    messages = [
        HumanMessage(
            content="型号：U27ETR仓库：C区-3号库，交期：还是 2026-11-10 吗？"
        ),
        HumanMessage(content="确认"),
    ]

    restored = _restore_confirmed_part_identity(request, messages)

    assert restored["items"][0]["part_name"] == "火花塞 U27ETR"
    assert restored["items"][0]["quantity"] == 10


def test_restores_part_model_from_current_user_message():
    request = _normalize_request(
        {"items": [{"part_name": "火花塞", "quantity": 10}]}
    )

    restored = _restore_confirmed_part_identity(
        request,
        [HumanMessage(content="下单10个火花塞，型号 U27ETR")],
    )

    assert restored["items"][0]["part_name"] == "火花塞 U27ETR"


def test_does_not_restore_old_model_without_confirmation():
    request = _normalize_request(
        {"items": [{"part_name": "火花塞", "quantity": 10}]}
    )

    restored = _restore_confirmed_part_identity(
        request,
        [
            HumanMessage(content="型号 U27ETR"),
            HumanMessage(content="这次买另一种火花塞"),
        ],
    )

    assert restored["items"][0]["part_name"] == "火花塞"


def test_does_not_choose_between_multiple_part_models():
    request = _normalize_request(
        {"items": [{"part_name": "火花塞", "quantity": 10}]}
    )

    restored = _restore_confirmed_part_identity(
        request,
        [HumanMessage(content="型号 U27ETR 或 CR9EIX，确认")],
    )

    assert restored["items"][0]["part_name"] == "火花塞"


def test_does_not_apply_one_model_to_multiple_items():
    request = _normalize_request(
        {
            "items": [
                {"part_name": "火花塞", "quantity": 10},
                {"part_name": "机油滤芯", "quantity": 2},
            ]
        }
    )

    restored = _restore_confirmed_part_identity(
        request,
        [HumanMessage(content="型号 U27ETR，确认")],
    )

    assert restored["items"][0]["part_name"] == "火花塞"


@pytest.mark.anyio
async def test_start_tool_passes_recovered_model_to_runner(monkeypatch):
    class FakeRunner:
        def __init__(self):
            self.request = None
            self.conversation_id = None

        async def start_background(self, **kwargs):
            self.request = kwargs["request"]
            self.conversation_id = kwargs["conversation_id"]
            return {
                "status": "running",
                "task_id": "TASK-20261102-MODEL001",
                "thread_id": "procurement:TASK-20261102-MODEL001",
                "conversation_id": kwargs["conversation_id"],
                "recoverable": True,
            }

    runner = FakeRunner()

    async def get_runner():
        return runner

    monkeypatch.setattr(task_tools, "get_task_runner", get_runner)
    runtime = SimpleNamespace(
        context=ProcurementContext(
            user_id="u1",
            username="张三",
            roles=("requester",),
            thread_id="conv-a",
        ),
        state={
            "messages": [
                HumanMessage(content="型号：U27ETR，仓库：C区-3号库"),
                HumanMessage(content="确认"),
            ]
        },
    )

    raw = await task_tools.start_procurement_task(
        {
            "items": [{"part_name": "火花塞", "quantity": 10}],
            "warehouse_location": "C区-3号库",
            "required_by": "2026-11-10",
            "preferred_supplier_name": "DENSO",
        },
        runtime,
    )

    result = json.loads(raw)
    assert result["task_id"] == "TASK-20261102-MODEL001"
    assert result["conversation_id"] == "conv-a"
    assert runner.conversation_id == "conv-a"
    assert runner.request["items"][0]["part_name"] == "火花塞 U27ETR"


@pytest.mark.anyio
async def test_start_tool_requires_conversation_context(monkeypatch):
    class FakeRunner:
        def __init__(self):
            self.called = False

        async def start_background(self, **kwargs):
            self.called = True
            return {"status": "running", "task_id": "TASK-1"}

    runner = FakeRunner()

    async def get_runner():
        return runner

    monkeypatch.setattr(task_tools, "get_task_runner", get_runner)
    runtime = SimpleNamespace(
        context=ProcurementContext(
            user_id="u1",
            username="张三",
            roles=("requester",),
        ),
        state={"messages": []},
    )

    raw = await task_tools.start_procurement_task(
        {"items": [{"part_name": "火花塞", "quantity": 1}]},
        runtime,
    )

    result = json.loads(raw)
    assert result["error"] == "CONVERSATION_CONTEXT_MISSING"
    assert result["status"] == "error"
    assert runner.called is False


def test_normalize_supplier_name_misplaced_in_id_field():
    request = normalize_procurement_request(
        {
            "items": [{"part_name": "电装 DENSO U27ETR", "quantity": 10}],
            "warehouse_location": "华东仓",
            "preferred_supplier_id": "DENSO",
        }
    )

    assert "preferred_supplier_id" not in request
    assert request["preferred_supplier_name"] == "DENSO"


def test_normalize_numeric_supplier_id_string():
    request = validate_procurement_request(
        {
            "items": [{"part_name": "火花塞", "quantity": 10}],
            "warehouse_location": "华东仓",
            "preferred_supplier_id": "31",
        }
    )

    assert request.preferred_supplier_id == 31


def test_validate_request_rejects_structural_error():
    with pytest.raises(ProcurementRequestInputError) as exc_info:
        validate_procurement_request(
            {
                "items": [
                    {"part_name": "火花塞", "quantity": "不是数量"}
                ],
                "warehouse_location": "华东仓",
            }
        )

    assert exc_info.value.errors
    assert exc_info.value.errors[0]["field"] == "items[0].quantity"


def test_task_tool_schemas():
    tools = {tool.name: tool for tool in build_procurement_task_tools()}
    assert set(tools) == {
        "start_procurement_task",
        "get_procurement_task",
        "resume_procurement_task",
    }
    assert "request" in tools["start_procurement_task"].args_schema.model_json_schema()["properties"]
    assert "task_id" in tools["get_procurement_task"].args_schema.model_json_schema()["properties"]
