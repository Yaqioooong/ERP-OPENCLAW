import json

import pytest

from agent.procurement.models import ProcurementRequest, RequestedItem
from agent.procurement.parser import LLMRequestParser


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("No fake response left")
        return FakeMessage(self.responses.pop(0))


def request_json() -> str:
    return json.dumps(
        {
            "items": [{"part_name": "活塞环组件", "quantity": 200}],
            "warehouse_location": "华东仓",
        },
        ensure_ascii=False,
    )


@pytest.mark.anyio
async def test_parse_plain_json_response():
    model = FakeChatModel([request_json()])
    parser = LLMRequestParser(model)

    result = await parser.parse("为华东仓补充 200 个活塞环组件")

    assert result.warehouse_location == "华东仓"
    assert result.items[0].quantity == 200
    assert len(model.calls) == 1


@pytest.mark.anyio
async def test_parse_tolerates_markdown_fence():
    model = FakeChatModel(["```json\n" + request_json() + "\n```"])
    parser = LLMRequestParser(model)

    result = await parser.parse("为华东仓补充 200 个活塞环组件")

    assert result.warehouse_location == "华东仓"


@pytest.mark.anyio
async def test_parse_retries_once_after_invalid_json():
    model = FakeChatModel(["不是 JSON", request_json()])
    parser = LLMRequestParser(model)

    result = await parser.parse("为华东仓补充 200 个活塞环组件")

    assert result.warehouse_location == "华东仓"
    assert len(model.calls) == 2


@pytest.mark.anyio
async def test_merge_request_patch_is_deterministic():
    model = FakeChatModel([])
    parser = LLMRequestParser(model)
    previous = ProcurementRequest(
        items=[RequestedItem(part_name="活塞环组件", quantity=200)]
    )

    merged = await parser.merge(
        previous,
        {"request_patch": {"warehouse_location": "华东仓"}},
    )

    assert merged.warehouse_location == "华东仓"
    assert merged.items[0].quantity == 200
    assert model.calls == []
