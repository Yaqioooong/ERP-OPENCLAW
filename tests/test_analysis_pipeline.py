from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.analysis_models import (
    AnalysisStatus,
    SandboxVerification,
)
from agent.procurement.analysis_pipeline import TrustedAnalysisPipeline
from tests.test_analysis_calculator import make_inputs

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


class FakeNarrator:
    async def narrate(self, *, analysis, validation):
        return "这是一段解释文字。"


class FakeSandbox:
    def __init__(self, status: str):
        self.status = status

    async def verify(self, analysis):
        return SandboxVerification(
            status=self.status,
            passed=self.status == "ok",
            error=None if self.status == "ok" else "sandbox unavailable",
        )


@pytest.mark.anyio
async def test_pipeline_returns_structured_report():
    request, candidates, plan = make_inputs()
    pipeline = TrustedAnalysisPipeline(
        narrator=FakeNarrator(),
        sandbox_verifier=FakeSandbox("ok"),
    )

    result = await pipeline.run(
        task_id="TASK-1",
        request=request,
        candidates=candidates,
        plan=plan,
        now=NOW,
    )

    assert result.status == AnalysisStatus.OK
    assert result.analysis.total_cost == 100
    assert "采购分析报告" in result.report_markdown
    assert "这是一段解释文字" in result.report_markdown


@pytest.mark.anyio
async def test_pipeline_fails_closed_on_sandbox_mismatch():
    request, candidates, plan = make_inputs()
    pipeline = TrustedAnalysisPipeline(
        sandbox_verifier=FakeSandbox("mismatch")
    )

    result = await pipeline.run(
        task_id="TASK-1",
        request=request,
        candidates=candidates,
        plan=plan,
        now=NOW,
    )

    assert result.status == AnalysisStatus.FAILED
    assert result.validation.passed is False
    assert result.error.code == "SANDBOX_RECOMPUTE_MISMATCH"


@pytest.mark.anyio
async def test_pipeline_degrades_when_sandbox_unavailable():
    request, candidates, plan = make_inputs()
    pipeline = TrustedAnalysisPipeline(
        sandbox_verifier=FakeSandbox("unavailable")
    )

    result = await pipeline.run(
        task_id="TASK-1",
        request=request,
        candidates=candidates,
        plan=plan,
        now=NOW,
    )

    assert result.status == AnalysisStatus.WARNING
    assert result.validation.passed is True
    assert result.sandbox_verification.status == "unavailable"
