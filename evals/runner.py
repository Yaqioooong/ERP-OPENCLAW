from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from agent.procurement.analysis_models import (
    AnalysisStatus,
    SandboxVerification,
)
from agent.procurement.analysis_pipeline import TrustedAnalysisPipeline
from agent.procurement.data_provider import McpProcurementDataProvider
from agent.procurement.dependencies import ProcurementDependencies
from agent.procurement.deterministic_plan_proposer import DeterministicPlanProposer
from agent.procurement.models import ProcurementRequest
from agent.procurement.planner import DefaultPlanBuilder
from agent.procurement.task_runner import ProcurementTaskRunner
from agent.procurement.workflow import compile_procurement_graph
from evals.factories import (
    FIXED_NOW,
    FakeOrderExecutor,
    MemoryApprovalRepository,
    MemoryTaskRepository,
    StaticParser,
    StaticProposer,
)
from evals.mock_tools import build_mock_tools
from evals.models import (
    ActionType,
    EvalResult,
    Scenario,
)


class FakeNarrator:
    async def narrate(self, *, analysis, validation):
        return "评测解释文字。"


class FakeSandbox:
    async def verify(self, analysis):
        return SandboxVerification(status="ok", passed=True)


class EvalRunner:
    async def run(self, scenario: Scenario) -> EvalResult:
        started = time.monotonic()
        failures: list[str] = []

        request_data = scenario.request or {}
        request = ProcurementRequest.model_validate(request_data)
        tools = build_mock_tools(scenario.faults)
        executor = FakeOrderExecutor(scenario.faults.order_mode)
        task_repository = MemoryTaskRepository()
        approval_repository = MemoryApprovalRepository()

        deps = ProcurementDependencies(
            request_parser=StaticParser(request),
            data_provider=McpProcurementDataProvider(
                tools, active_supplier_statuses={1}
            ),
            plan_proposer=DeterministicPlanProposer(),
            plan_builder=DefaultPlanBuilder(),
            approval_repository=approval_repository,
            order_executor=executor,
            now=lambda: FIXED_NOW,
            execution_enabled=True,
            analysis_pipeline=TrustedAnalysisPipeline(
                narrator=FakeNarrator(),
                sandbox_verifier=FakeSandbox(),
            ),
        )

        saver = MemorySaver()
        graph = compile_procurement_graph(deps, saver)
        runner = ProcurementTaskRunner(
            graph=graph,
            task_repository=task_repository,
            now=lambda: FIXED_NOW,
        )

        task_id = None
        result = None
        report = None
        error_code_override = None
        final_status_override = None

        for action in scenario.actions:
            if action.type == ActionType.START:
                result = await runner.start(
                    raw_request=scenario.raw_request,
                    user_id=action.user_id,
                    username="张三",
                    request=request_data,
                    roles=action.roles,
                )
                task_id = result.task_id
            elif action.type == ActionType.RESTART:
                graph = compile_procurement_graph(deps, saver)
                runner = ProcurementTaskRunner(
                    graph=graph,
                    task_repository=task_repository,
                    now=lambda: FIXED_NOW,
                )
            elif action.type == ActionType.RESUME:
                if task_id is None:
                    failures.append("RESUME_WITHOUT_START")
                    break
                try:
                    result = await runner.resume(
                        task_id=task_id,
                        user_id=action.user_id,
                        username="张三",
                        resume_data=action.resume or {},
                        roles=action.roles,
                    )
                except PermissionError:
                    result = None
                    error_code_override = "FORBIDDEN"
                    final_status_override = "failed"

            if task_id:
                snapshot = await graph.aget_state(
                    {"configurable": {"thread_id": f"procurement:{task_id}"}}
                )
                report = (snapshot.values or {}).get("analysis_report")

        order_created = executor.create_calls
        tool_calls = sum(tool.calls for tool in tools.values())
        final_status = (
            final_status_override
            if final_status_override is not None
            else (result.status if result else None)
        )
        execution_status = (
            result.state_summary.get("execution_status") if result else None
        )
        last_error = (
            (result.state_summary.get("last_error") or {})
            if result else {}
        )
        error_code = (
            error_code_override
            if error_code_override is not None
            else last_error.get("code")
        )
        error_codes = [error_code] if error_code else []
        error_codes.extend(
            item.get("code")
            for item in (last_error.get("errors") or [])
            if isinstance(item, dict)
        )
        approval_status = (
            result.state_summary.get("approval_status") if result else None
        )
        plan_version = (
            result.state_summary.get("plan_version") if result else None
        )

        expect = scenario.expect
        if expect.final_status is not None and final_status != expect.final_status:
            failures.append(
                f"status: expected {expect.final_status}, got {final_status}"
            )
        if (
            expect.execution_status is not None
            and execution_status != expect.execution_status
        ):
            failures.append(
                "execution_status: expected "
                f"{expect.execution_status}, got {execution_status}"
            )
        if expect.error_code is not None and error_code != expect.error_code:
            failures.append(
                f"error_code: expected {expect.error_code}, got {error_code}"
            )
        for code in expect.error_codes_include:
            if code not in error_codes:
                failures.append(f"missing error code: {code}")
        if expect.order_created is not None:
            actual = order_created > 0
            if actual != expect.order_created:
                failures.append(
                    f"order_created: expected {expect.order_created}, got {actual}"
                )
        if (
            expect.min_plan_version is not None
            and (plan_version or 0) < expect.min_plan_version
        ):
            failures.append(
                f"plan_version {plan_version} < {expect.min_plan_version}"
            )
        if (
            expect.approval_status is not None
            and approval_status != expect.approval_status
        ):
            failures.append(
                f"approval_status: expected {expect.approval_status}, "
                f"got {approval_status}"
            )
        if (
            expect.max_order_created is not None
            and order_created > expect.max_order_created
        ):
            failures.append(
                f"order count {order_created} > {expect.max_order_created}"
            )
        if expect.forbidden_order and order_created:
            failures.append("order was created but should be forbidden")
        if expect.max_tool_calls is not None and tool_calls > expect.max_tool_calls:
            failures.append(
                f"tool calls {tool_calls} > {expect.max_tool_calls}"
            )
        if expect.report_required and not report:
            failures.append("report missing")
        for text in expect.report_contains:
            if not report or text not in report:
                failures.append(f"report missing text: {text}")

        return EvalResult(
            scenario_id=scenario.id,
            group=scenario.group,
            title=scenario.title,
            passed=not failures,
            duration_ms=int((time.monotonic() - started) * 1000),
            failures=failures,
            final_status=final_status,
            execution_status=execution_status,
            error_code=error_code,
            order_created=order_created,
            tool_calls=tool_calls,
            report=report,
        )
