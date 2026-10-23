from agent.tools.procurement_task_tools import (
    _normalize_request,
    build_procurement_task_tools,
)


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


def test_task_tool_schemas():
    tools = {tool.name: tool for tool in build_procurement_task_tools()}
    assert set(tools) == {
        "start_procurement_task",
        "get_procurement_task",
        "resume_procurement_task",
    }
    assert "request" in tools["start_procurement_task"].args_schema.model_json_schema()["properties"]
    assert "task_id" in tools["get_procurement_task"].args_schema.model_json_schema()["properties"]
