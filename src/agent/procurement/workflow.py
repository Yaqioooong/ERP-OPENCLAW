from langgraph.constants import START, END
from langgraph.graph import StateGraph

from agent.procurement.dependencies import ProcurementDependencies
from agent.procurement.nodes import build_nodes
from agent.procurement.routing import after_authorize, after_validate_request, after_parse_request, \
    after_apply_clarification, after_analyze, after_validate_plan, after_prepare_approval, after_request_approval, \
    after_finalize_approval, after_create_order, after_verify_order
from agent.procurement.state import ProcurementState
from agent.schema import ProcurementContext


def build_procurement_graph(deps: ProcurementDependencies) -> StateGraph:
    nodes = build_nodes(deps)
    builder = StateGraph(ProcurementState, context_schema=ProcurementContext)

    for name, fn in nodes.items():
        # nodes.items() 返回键值对 (key, value)，所以 for name, fn in ... 是在做元组解包，等价于builder.add_node("retrieve", retrieve_fn)
        builder.add_node(name, fn)

    builder.add_edge(START, "authorize")
    builder.add_conditional_edges(
        "authorize",
        after_authorize,
        {"parse": "parse_request", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "parse_request",
        after_parse_request,
        {"validate_request": "validate_request", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "validate_request",
        after_validate_request,
        {"ask_user": "ask_user", "analyze": "analyze_procurement", "fail": "fail_task"}
    )
    builder.add_edge("ask_user", "apply_clarification")
    builder.add_conditional_edges(
        "apply_clarification",
        after_apply_clarification,
        {"validate_request": "validate_request", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "analyze_procurement",
        after_analyze,
        {"validate_plan": "validate_plan", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "validate_plan",
        after_validate_plan,
        {"prepare_approval": "prepare_approval", "analyze": "analyze_procurement", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "prepare_approval",
        after_prepare_approval,
        {"request_approval": "request_approval", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "request_approval",
        after_request_approval,
        {"finalize": "finalize_approval", "fail": "fail_task"},
    )
    builder.add_conditional_edges(
        "finalize_approval",
        after_finalize_approval,
        {
            "create_order": "create_order",
            "reject": "reject_task",
            "analyze": "analyze_procurement",
            "fail": "fail_task"
        },
    )
    builder.add_conditional_edges(
        "create_order",
        after_create_order,
        {"verify": "verify_order", "pending": "pending_execution", "fail": "fail_task"}
    )
    builder.add_conditional_edges(
        "verify_order",
        after_verify_order,
        {"complete": "complete_task", "fail": "fail_task"}
    )

    for terminal in ("complete_task", "pending_execution", "fail_task", "reject_task"):
        builder.add_edge(terminal, END)

    return builder


def compile_procurement_graph(deps: ProcurementDependencies, checkpointer):
    """编译图。checkpointer 由调用方传入：生产用 Redis，测试用 MemorySaver。"""
    return build_procurement_graph(deps).compile(checkpointer=checkpointer)
