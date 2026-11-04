"""委派契约回归测试（规格 specs/010-delegation-contract）。

覆盖：
- 下行简报校验：必需段缺失拦回、可补齐段自动填充、非 task 工具不干预；
- 上行回传解析：规范四段 → structured、残缺 → degraded 且指向文件产物；
- 文档一致性：三处提示词/文档的段落名不与契约漂移。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middlewares.delegation_contract import DelegationContractMiddleware
from agent.subagents.contract import (
    BRIEF_SECTIONS,
    STATUS_DEGRADED,
    STATUS_STRUCTURED,
    build_default_sections,
    parse_report,
    patch_brief,
    validate_brief,
)

ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = ROOT / "src/agent/memory/AGENTS.md"
MAIN_PROMPT = ROOT / "src/agent/memory/prompts.py"
README = ROOT / "README.md"


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def info(self, msg: str, *args) -> None:
        self.events.append(("info", msg % args if args else msg))

    def warning(self, msg: str, *args) -> None:
        self.events.append(("warning", msg % args if args else msg))


def _request(description: str, subagent_type: str = "procurement-analyst",
             name: str = "task") -> ToolCallRequest:
    runtime = SimpleNamespace(
        context=SimpleNamespace(user_id="u-1", username="张三", roles=("buyer",))
    )
    return ToolCallRequest(
        tool_call={
            "name": name,
            "args": {"description": description, "subagent_type": subagent_type},
            "id": "call-1",
        },
        tool=None,
        state={},
        runtime=runtime,
    )


FULL_BRIEF = """【任务目标】
比价火花塞供应商

【用户偏好】
输出格式：表格
货币单位：CNY

【需求正文】
帮我看看 U27ETR 火花塞哪家便宜

【输出要求】
1. 报告写到 /analysis/
2. 摘要 300 字
"""


# ---------------------------------------------------------------- 下行契约

def test_valid_brief_passes_untouched():
    check = validate_brief(FULL_BRIEF)
    assert check.ok
    assert check.missing == () and check.empty == ()
    assert patch_brief(FULL_BRIEF, build_default_sections(
        "procurement-analyst")) == FULL_BRIEF


def test_missing_hard_section_is_rejected_without_dispatch():
    middleware = DelegationContractMiddleware(logger=_Logger())
    called = []

    def handler(request):
        called.append(request)
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = middleware.wrap_tool_call(_request("【任务目标】比价"), handler)

    assert called == []                      # 没有派发子 Agent
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "【需求正文】" in result.content
    assert "请补齐后重新调用 task" in result.content


def test_empty_section_body_counts_as_missing():
    middleware = DelegationContractMiddleware(logger=_Logger())
    called = []

    def handler(request):
        called.append(request)
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = middleware.wrap_tool_call(
        _request("【任务目标】比价\n【需求正文】\n\n【用户偏好】表格"), handler
    )

    assert called == []
    assert result.status == "error"
    assert "【需求正文】" in result.content


def test_missing_soft_sections_are_patched_from_runtime_context():
    logger = _Logger()
    middleware = DelegationContractMiddleware(logger=logger)
    seen: list[str] = []

    def handler(request):
        seen.append(request.tool_call["args"]["description"])
        return ToolMessage(content="【报告路径】/analysis/r.md\n【分析内容摘要】s\n"
                                    "【分析结论】c\n【采购建议】p",
                           tool_call_id="call-1")

    brief = "【任务目标】比价\n【需求正文】火花塞 U27ETR"
    result = middleware.wrap_tool_call(_request(brief), handler)

    assert result.status == "success"
    assert len(seen) == 1
    patched = seen[0]
    assert "【用户偏好】" in patched and "【输出要求】" in patched
    assert "u-1" in patched and "张三" in patched   # 身份来自 runtime.context
    assert any("brief_patched" in msg for level, msg in logger.events if level == "info")


def test_non_task_tools_are_not_touched():
    middleware = DelegationContractMiddleware(logger=_Logger())
    payload = ToolMessage(content="随便什么输出", tool_call_id="call-1")

    result = middleware.wrap_tool_call(
        _request("没有段落", name="order_search_details"), lambda r: payload
    )

    assert result is payload


# ---------------------------------------------------------------- 上行契约

def test_structured_report_is_normalized():
    raw = (
        "【报告路径】\n/analysis/report_1.md\n\n"
        "【分析内容摘要】\n两家供应商价差 8%。\n\n"
        "【分析结论】\n1. A 更便宜\n2. B 交期更短\n\n"
        "【采购建议】\n- 选 A，锁 3 个月价"
    )
    middleware = DelegationContractMiddleware(logger=_Logger())
    result = middleware.wrap_tool_call(
        _request(FULL_BRIEF), lambda r: ToolMessage(content=raw, tool_call_id="call-1")
    )

    assert result.status == "success"
    assert "status=structured" in result.content
    assert "agent=procurement-analyst" in result.content
    assert "/analysis/report_1.md" in result.content


def test_alias_headers_are_accepted():
    raw = ("【报告路径】/analysis/r.md\n【摘要】s\n【结论】c\n【建议】p")
    envelope = parse_report(raw, "procurement-analyst")
    assert envelope.status == STATUS_STRUCTURED
    assert envelope.fields["【分析结论】"] == "c"


def test_incomplete_report_degrades_and_points_to_file():
    logger = _Logger()
    middleware = DelegationContractMiddleware(logger=logger)
    raw = "我查了供应商数据，A 家便宜一些，报告在 /analysis/report_9.md 里。"
    result = middleware.wrap_tool_call(
        _request(FULL_BRIEF), lambda r: ToolMessage(content=raw, tool_call_id="call-1")
    )

    assert result.status == "success"          # 工具本身没失败
    assert "status=degraded" in result.content  # 但回传被标记降级
    assert "/analysis/report_9.md" in result.content
    assert "先 read_file 读取该文件" in result.content
    assert raw in result.content               # 原文保留，不丢信息
    assert any("report_degraded" in msg for level, msg in logger.events
               if level == "warning")


def test_empty_report_is_degraded_not_crash():
    envelope = parse_report("", "procurement-analyst")
    assert envelope.status == STATUS_DEGRADED
    assert envelope.report_path is None


def test_order_report_contract_uses_operation_fields():
    raw = "【操作结果】成功\n【变更字段】expectedDeliveryDate=2026-11-20\n【备注】已通知供应商"
    envelope = parse_report(raw, "procurement-order")
    assert envelope.status == STATUS_STRUCTURED

    degraded = parse_report("改好了", "procurement-order")
    assert degraded.status == STATUS_DEGRADED
    assert "【操作结果】" in degraded.missing


def test_error_tool_message_passes_through():
    middleware = DelegationContractMiddleware(logger=_Logger())
    original = ToolMessage(content="boom", tool_call_id="call-1", status="error")
    assert middleware.wrap_tool_call(_request(FULL_BRIEF), lambda r: original) is original


# ------------------------------------------------------------ 文档一致性

def test_all_prompt_sources_use_the_same_brief_sections():
    """提示词文档是软约束，至少不能各写各的。"""
    for path in (AGENTS_MD, MAIN_PROMPT, README):
        text = path.read_text(encoding="utf-8")
        missing = [s for s in BRIEF_SECTIONS if s not in text]
        assert not missing, f"{path.name} 缺少委派段落：{missing}"


def test_prompt_sources_point_to_middleware_contract():
    """文档要说明硬约束在哪，避免把软模板当强保证。"""
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "DelegationContractMiddleware" in text
