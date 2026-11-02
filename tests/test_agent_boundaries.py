"""订单写入边界回归：创建订单只能走采购任务工作流。

对应规格 specs/009-single-order-write-path/spec.md 的 AC-1/2/3/4/5/7。
"""
from pathlib import Path

from agent.memory.prompts import system_prompt
from agent.subagents.loader import load_subagent_configs
from agent.tools.order_guard import (
    _missing_update_fields,
    build_guarded_order_tools,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = REPO_ROOT / "src/agent/memory/AGENTS.md"


def _configs() -> dict[str, dict]:
    configs = load_subagent_configs()
    assert configs, "未加载到任何子 Agent 配置"
    return {config["name"]: config for config in configs}


def test_subagents_never_expose_order_create():
    """AC-1：任何子 Agent 都不得暴露订单创建工具。"""
    exposed = {
        name: list(config.get("tools") or [])
        for name, config in _configs().items()
    }
    assert all(
        "order_create" not in tools for tools in exposed.values()
    ), exposed


def test_order_subagent_scope_is_existing_orders_only():
    """AC-3/AC-5：订单子 Agent 只维护存量订单，且不保留死配置 interrupt_on。"""
    config = _configs()["procurement-order"]
    tools = list(config["tools"])
    assert "order_update" in tools
    assert "order_search_details" in tools
    assert "interrupt_on" not in config


def test_order_subagent_description_has_no_create_trigger():
    """AC-7：描述中不出现创建类触发词，避免与任务工作流抢路由。"""
    description = _configs()["procurement-order"]["description"]
    assert "下单" not in description
    assert "创建订单" not in description
    assert "start_procurement_task" in description


def test_main_prompt_routes_creation_to_workflow():
    """AC-2：主 Agent 提示词把创建交给任务工作流，把存量订单交给子 Agent。"""
    assert "start_procurement_task" in system_prompt
    assert "get_procurement_task" in system_prompt
    assert "procurement-order" in system_prompt


def test_agents_md_safety_rule_points_to_workflow():
    """AC-2：/AGENTS.md 的安全边界不再要求所有订单操作走子 Agent。"""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "所有订单操作（创建/修改）必须经过" not in text
    assert "start_procurement_task" in text


def test_update_guard_still_checks_detail_fields():
    """AC-4：修改订单的确定性字段校验仍然生效。"""
    missing = _missing_update_fields(
        12,
        {"orderDetail": [{"partId": 65, "quantity": 2}]},
    )
    assert any("unitPrice" in item for item in missing)

    missing_order_id = _missing_update_fields(None, {"remark": "改备注"})
    assert any("order_id" in item for item in missing_order_id)


def test_guard_keeps_update_wrapped_and_read_tools_untouched():
    """AC-4：order_update 仍被守卫替换；只读工具原样透传。"""

    class FakeTool:
        def __init__(self, name: str):
            self.name = name

        async def ainvoke(self, payload):  # pragma: no cover - 结构性占位
            return payload

    tools = [
        FakeTool("order_create"),
        FakeTool("order_update"),
        FakeTool("order_search_details"),
    ]
    guarded = build_guarded_order_tools(tools)

    assert [tool.name for tool in guarded] == [
        "order_create",
        "order_update",
        "order_search_details",
    ]
    assert guarded[2] is tools[2]

