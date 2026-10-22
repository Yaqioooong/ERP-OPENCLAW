from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Callable
from zoneinfo import ZoneInfo

from agent.procurement.approval_repository import SqliteApprovalRepository
from agent.procurement.config import PROCUREMENT_MAX_PLAN_REVISIONS, PROCUREMENT_MAX_CLARIFICATION_ROUNDS, \
    PROCUREMENT_APPROVAL_TTL_MINUTES, PROCUREMENT_TIMEZONE, PROCUREMENT_ACTIVE_SUPPLIER_STATUSES, PROCUREMENT_DB_PATH, \
    PROCUREMENT_EXECUTION_ENABLED
from agent.procurement.data_provider import McpProcurementDataProvider
from agent.procurement.deterministic_plan_proposer import DeterministicPlanProposer
from agent.procurement.models import ProcurementRequest, CandidateSet, PlanProposal
from agent.procurement.order_executor import DisabledOrderExecutor
from agent.procurement.parser import LLMRequestParser
from agent.procurement.planner import DefaultPlanBuilder
from agent.procurement.ports import RequestParser, ProcurementDataProvider, PlanProposer, ApprovalRepository, \
    OrderExecutionService

_PROCUREMENT_GRAPH = None

class PlanBuilder(Protocol):
    def build(
            self,
            *,
            task_id: str,
            version: int,
            request: ProcurementRequest,
            candidates: CandidateSet,
            proposal: PlanProposal,
            now: datetime,
    ):
        ...


@dataclass(frozen=True)
class ProcurementDependencies:
    request_parser: RequestParser
    data_provider: ProcurementDataProvider
    plan_proposer: PlanProposer
    plan_builder: PlanBuilder

    approval_repository: ApprovalRepository
    order_executor: OrderExecutionService
    now: Callable[[], datetime]
    max_plan_revisions: int = PROCUREMENT_MAX_PLAN_REVISIONS
    max_clarification_rounds: int = PROCUREMENT_MAX_CLARIFICATION_ROUNDS
    approval_ttl: timedelta = timedelta(minutes=PROCUREMENT_APPROVAL_TTL_MINUTES)
    execution_enabled: bool = False


def utc_now_in_business_tz() -> datetime:
    return datetime.now(ZoneInfo(PROCUREMENT_TIMEZONE))


async def build_production_dependencies() -> ProcurementDependencies:
    # 延迟 import：agent.config 在 import 时会创建 Redis 客户端、执行 STORE.setup()，
    # 不能放在模块顶层，否则纯规则测试/图测试会被迫依赖 Redis。
    from agent.config import SUMMARY_MODEL
    from agent.tools.mcp_client import load_mcp_tools
    # 注意：load_mcp_tools 会新建 MCP 连接；后续可抽共享 ToolRegistry
    _, analyst_tools, order_tools, _ = await load_mcp_tools()
    tools = {t.name: t for t in [*analyst_tools, *order_tools]}

    return ProcurementDependencies(
        request_parser=LLMRequestParser(SUMMARY_MODEL),
        data_provider=McpProcurementDataProvider(
            tools,
            active_supplier_statuses=PROCUREMENT_ACTIVE_SUPPLIER_STATUSES,
        ),
        plan_proposer=DeterministicPlanProposer(),
        plan_builder=DefaultPlanBuilder(),
        approval_repository=SqliteApprovalRepository(PROCUREMENT_DB_PATH),
        # 本步强制禁用真实执行；PROCUREMENT_EXECUTION_ENABLED 只作为
        # deeper_3 选择真实执行器的开关预留，不在这里放开。
        order_executor=DisabledOrderExecutor(),
        now=utc_now_in_business_tz,
        execution_enabled=PROCUREMENT_EXECUTION_ENABLED,
    )


async def get_procurement_graph():
    """懒加载编译图。必须在 initialize_redis_checkpointer() 之后调用。"""
    from agent.procurement.workflow import compile_procurement_graph

    global _PROCUREMENT_GRAPH
    if _PROCUREMENT_GRAPH is None:
        from agent import config as agent_config

        deps = await build_production_dependencies()
        # 必须读 agent_config.CHECKPOINTER 的当前值：
        # initialize_redis_checkpointer() 是在运行时给模块属性赋值的，
        # `from agent.config import CHECKPOINTER` 只会拿到 import 时的 None。
        _PROCUREMENT_GRAPH = compile_procurement_graph(
            deps, agent_config.CHECKPOINTER
        )
    return _PROCUREMENT_GRAPH
