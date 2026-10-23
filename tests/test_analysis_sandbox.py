from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from agent.procurement.analysis_models import AnalysisLine, StructuredAnalysis
from agent.procurement.analysis_sandbox import OpenSandboxVerifier

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


def make_analysis() -> StructuredAnalysis:
    return StructuredAnalysis(
        task_id="TASK-1",
        plan_version=1,
        plan_hash="HASH",
        computed_at=NOW,
        total_cost=Decimal("100.00"),
        budget=Decimal("200.00"),
        inventory_shortage=3,
        lines=[
            AnalysisLine(
                part_id=1,
                part_name="活塞环",
                quantity=10,
                unit_price=Decimal("10.00"),
                subtotal=Decimal("100.00"),
                supplier_id=4,
                inventory_gap=3,
            )
        ],
    )


class FakeSandbox:
    def __init__(self, *, timeout=False):
        self.timeout = timeout
        self.cleaned = False

    def upload_files(self, files):
        return [SimpleNamespace(path=path, error=None) for path, _ in files]

    def execute(self, command, *, timeout=None):
        if self.timeout:
            raise TimeoutError("sandbox timeout")
        if command.startswith("rm "):
            self.cleaned = True
        return SimpleNamespace(exit_code=0, output="")

    def download_files(self, paths):
        return [
            SimpleNamespace(
                path=paths[0],
                content=b'{"passed": true, "issues": []}',
                error=None,
            )
        ]


@pytest.mark.anyio
async def test_sandbox_verifier_cleans_up():
    sandbox = FakeSandbox()
    result = await OpenSandboxVerifier(sandbox, timeout_seconds=1).verify(
        make_analysis()
    )

    assert result.passed is True
    assert sandbox.cleaned is True


@pytest.mark.anyio
async def test_sandbox_timeout_is_reported_not_raised():
    sandbox = FakeSandbox(timeout=True)
    result = await OpenSandboxVerifier(sandbox, timeout_seconds=1).verify(
        make_analysis()
    )

    assert result.status in {"timeout", "unavailable"}
    assert result.passed is False
