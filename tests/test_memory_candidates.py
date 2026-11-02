from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.memory.candidate_generator import MemoryCandidateGenerator


class FakeModel:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


def tool_message(name, data, *, tool_call_id="tool-1", ok=True):
    return ToolMessage(
        content=json.dumps({"ok": ok, "data": data}, ensure_ascii=False),
        name=name,
        tool_call_id=tool_call_id,
    )


@pytest.mark.anyio
async def test_pure_greeting_has_no_candidates():
    generator = MemoryCandidateGenerator()
    result = await generator.generate(
        [HumanMessage(content="你好", id="h1")], thread_id="t"
    )
    assert result == []


@pytest.mark.anyio
async def test_greeting_plus_procurement_is_not_skipped():
    generator = MemoryCandidateGenerator()
    result = await generator.generate(
        [HumanMessage(content="你好，帮我比较供应商", id="h1")],
        thread_id="t",
    )
    assert len(result) == 1
    candidate = result[0]
    assert candidate.memory_type == "recent_queries"
    assert candidate.origin == "inferred"
    assert candidate.source_message_id == "h1"
    assert candidate.value == "你好，帮我比较供应商"


@pytest.mark.anyio
async def test_explicit_preference_uses_llm_and_explicit_origin():
    model = FakeModel(
        {"candidates": [{"memory_type": "preferred_output", "value": "table", "confidence": 0.95}]}
    )
    generator = MemoryCandidateGenerator(model)
    result = await generator.generate(
        [HumanMessage(content="以后都用表格", id="h1")],
        thread_id="t",
    )
    assert len(result) == 1
    candidate = result[0]
    assert candidate.memory_type == "preferred_output"
    assert candidate.value == "table"
    assert candidate.origin == "explicit"
    assert candidate.source == "user_message"
    assert candidate.confidence >= 0.8


@pytest.mark.anyio
async def test_explicit_llm_invalid_enum_is_dropped():
    model = FakeModel(
        {"candidates": [{"memory_type": "preferred_output", "value": "pie", "confidence": 0.9}]}
    )
    generator = MemoryCandidateGenerator(model)
    result = await generator.generate(
        [HumanMessage(content="以后都用饼图", id="h1")],
        thread_id="t",
    )
    assert result == []


@pytest.mark.anyio
async def test_supplier_comes_from_tool_result_not_ai_summary():
    messages = [
        HumanMessage(content="帮我分析供应商", id="h1"),
        AIMessage(content="B 公司看起来不错"),
        tool_message("supplier_get", {"name": "C 公司"}),
    ]
    generator = MemoryCandidateGenerator()
    result = await generator.generate(messages, thread_id="t")
    suppliers = [c for c in result if c.memory_type == "recent_suppliers"]
    assert [c.value for c in suppliers] == ["C 公司"]
    assert suppliers[0].origin == "inferred"
    assert suppliers[0].source == "tool_result"
    assert suppliers[0].source_message_id == "tool-1"
    assert all("B 公司" != c.value for c in result)


@pytest.mark.anyio
async def test_negative_supplier_is_filtered():
    messages = [
        HumanMessage(content="不要选择 A 公司的采购方案", id="h1"),
        tool_message("supplier_get", {"name": "A 公司"}),
    ]
    generator = MemoryCandidateGenerator()
    result = await generator.generate(messages, thread_id="t")
    assert all(c.value != "A 公司" for c in result)


@pytest.mark.anyio
async def test_broad_supplier_search_is_below_threshold():
    messages = [
        HumanMessage(content="帮我比较供应商", id="h1"),
        tool_message("supplier_query", [{"name": "A"}, {"name": "B"}]),
    ]
    generator = MemoryCandidateGenerator()
    result = await generator.generate(messages, thread_id="t")
    assert all(c.memory_type != "recent_suppliers" for c in result)


@pytest.mark.anyio
async def test_part_query_supplier_name_is_collected():
    messages = [
        HumanMessage(content="帮我查物料", id="h1"),
        tool_message("part_query", [{"supplier_name": "D 公司", "name": "刹车片"}]),
    ]
    generator = MemoryCandidateGenerator()
    result = await generator.generate(messages, thread_id="t")
    suppliers = [c.value for c in result if c.memory_type == "recent_suppliers"]
    assert suppliers == ["D 公司"]


@pytest.mark.anyio
async def test_failed_tool_result_is_ignored():
    messages = [
        HumanMessage(content="帮我查供应商", id="h1"),
        tool_message("supplier_get", {"name": "A 公司"}, ok=False),
    ]
    generator = MemoryCandidateGenerator()
    result = await generator.generate(messages, thread_id="t")
    assert all(c.memory_type != "recent_suppliers" for c in result)


@pytest.mark.anyio
async def test_candidate_has_required_fields():
    messages = [
        HumanMessage(content="帮我分析供应商", id="h1"),
        tool_message("supplier_get", {"name": "C 公司"}),
    ]
    result = await MemoryCandidateGenerator().generate(messages, thread_id="t")
    supplier = next(c for c in result if c.memory_type == "recent_suppliers")
    assert supplier.created_at.tzinfo is not None
    assert 0 <= supplier.confidence <= 1
    assert supplier.source_message_id
    assert supplier.origin == "inferred"
