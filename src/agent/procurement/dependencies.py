from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Callable

from agent.procurement.approval_repository import PostgresApprovalRepository
from agent.procurement.config import PROCUREMENT_MAX_PLAN_REVISIONS, PROCUREMENT_MAX_CLARIFICATION_ROUNDS, \
    PROCUREMENT_APPROVAL_TTL_MINUTES, PROCUREMENT_ACTIVE_SUPPLIER_STATUSES, PROCUREMENT_DATABASE_URL, \
    PROCUREMENT_EXECUTION_ENABLED, utc_now_in_business_tz
from agent.procurement.database import Database
from agent.procurement.data_provider import McpProcurementDataProvider
from agent.procurement.deterministic_plan_proposer import DeterministicPlanProposer
from agent.procurement.erp_order_gateway import McpErpOrderGateway
from agent.procurement.execution_repository import PostgresExecutionRepository
from agent.procurement.models import ProcurementRequest, CandidateSet, PlanProposal
from agent.procurement.order_execution import SafeOrderExecutionService
from agent.procurement.order_reconciliation import OrderReconciler
from agent.procurement.reconciliation_worker import ReconciliationWorker
from agent.procurement.order_executor import DisabledOrderExecutor
from agent.procurement.parser import LLMRequestParser
from agent.procurement.planner import DefaultPlanBuilder
from agent.procurement.ports import RequestParser, ProcurementDataProvider, PlanProposer, ApprovalRepository, \
    OrderExecutionService
from agent.procurement.task_repository import PostgresTaskRepository
from agent.procurement.task_runner import ProcurementTaskRunner

_PROCUREMENT_GRAPH = None
_PROCUREMENT_DEPS = None
_RECONCILER = None
_WORKER = None
_DATABASE = None


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
    max_task_resumes: int = 3
    order_reconciler: OrderReconciler | None = None


async def get_database() -> Database:
    global _DATABASE
    if _DATABASE is None:
        _DATABASE = Database(PROCUREMENT_DATABASE_URL)
        await _DATABASE.connect()
    return _DATABASE


async def build_production_dependencies() -> ProcurementDependencies:
    from agent.config import SUMMARY_MODEL
    from agent.tools.mcp_client import load_mcp_tools

    _, analyst_tools, order_tools, _ = await load_mcp_tools()
    tools = {t.name: t for t in [*analyst_tools, *order_tools]}

    database = await get_database()
    approval_repository = PostgresApprovalRepository(database)
    execution_repository = PostgresExecutionRepository(database)
    await approval_repository.setup()
    await execution_repository.setup()

    gateway = McpErpOrderGateway(tools)
    if PROCUREMENT_EXECUTION_ENABLED:
        order_executor = SafeOrderExecutionService(
            execution_repository=execution_repository,
            approval_repository=approval_repository,
            gateway=gateway,
            now=utc_now_in_business_tz,
        )
    else:
        order_executor = DisabledOrderExecutor()

    reconciler = OrderReconciler(
        executions=execution_repository,
        gateway=gateway,
        now=utc_now_in_business_tz,
    )

    return ProcurementDependencies(
        request_parser=LLMRequestParser(SUMMARY_MODEL),
        data_provider=McpProcurementDataProvider(
            tools,
            active_supplier_statuses=PROCUREMENT_ACTIVE_SUPPLIER_STATUSES,
        ),
        plan_proposer=DeterministicPlanProposer(),
        plan_builder=DefaultPlanBuilder(),
        approval_repository=approval_repository,
        order_executor=order_executor,
        now=utc_now_in_business_tz,
        execution_enabled=PROCUREMENT_EXECUTION_ENABLED,
        order_reconciler=reconciler,
    )


async def get_procurement_dependencies() -> ProcurementDependencies:
    global _PROCUREMENT_DEPS
    if _PROCUREMENT_DEPS is None:
        _PROCUREMENT_DEPS = await build_production_dependencies()
    return _PROCUREMENT_DEPS


async def get_procurement_graph():
    """懒加载编译图。必须在 initialize_redis_checkpointer() 之后调用。"""
    from agent.procurement.workflow import compile_procurement_graph

    global _PROCUREMENT_GRAPH
    if _PROCUREMENT_GRAPH is None:
        from agent import config as agent_config

        deps = await get_procurement_dependencies()
        # 必须读 agent_config.CHECKPOINTER 的当前值：
        # initialize_redis_checkpointer() 是在运行时给模块属性赋值的，
        # `from agent.config import CHECKPOINTER` 只会拿到 import 时的 None。
        _PROCUREMENT_GRAPH = compile_procurement_graph(
            deps, agent_config.CHECKPOINTER
        )
    return _PROCUREMENT_GRAPH


_TASK_RUNNER: ProcurementTaskRunner | None = None


async def get_task_runner() -> ProcurementTaskRunner:
    global _TASK_RUNNER
    if _TASK_RUNNER is None:
        from agent import config as agent_config
        if agent_config.CHECKPOINTER is None:
            raise RuntimeError(
                "CheckPointer 未初始化：请先调用 initialize_redis_checkpointer()"
            )

        graph = await get_procurement_graph()
        repository = PostgresTaskRepository(await get_database())
        await repository.setup()
        _TASK_RUNNER = ProcurementTaskRunner(
            graph=graph,
            task_repository=repository
        )
        await _TASK_RUNNER.reconcile()

    return _TASK_RUNNER


async def get_order_reconciler() -> OrderReconciler:
    global _RECONCILER
    if _RECONCILER is None:
        deps = await get_procurement_dependencies()
        if deps.order_reconciler is None:
            raise RuntimeError("订单核验器未初始化")
        _RECONCILER = deps.order_reconciler
    return _RECONCILER


async def get_reconciliation_worker() -> ReconciliationWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = ReconciliationWorker(
            reconciler=await get_order_reconciler()
        )
    return _WORKER


async def stop_reconciliation_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        await _WORKER.stop()
        _WORKER = None


async def close_task_runner() -> None:
    global _TASK_RUNNER, _PROCUREMENT_GRAPH, _PROCUREMENT_DEPS, _RECONCILER, _DATABASE
    _TASK_RUNNER = None
    _PROCUREMENT_GRAPH = None
    _PROCUREMENT_DEPS = None
    _RECONCILER = None
    if _DATABASE is not None:
        await _DATABASE.close()
        _DATABASE = None
