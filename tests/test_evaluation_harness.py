import pytest

from evals.metrics import aggregate
from evals.models import EvalResult
from evals.runner import EvalRunner
from evals.scenarios import SCENARIOS


def test_has_30_unique_scenarios():
    assert len(SCENARIOS) == 30
    assert len({scenario.id for scenario in SCENARIOS}) == 30


@pytest.mark.anyio
async def test_eval_runner_runs_approval_scenario():
    scenario = next(
        item for item in SCENARIOS if item.id == "AP-02"
    )

    result = await EvalRunner().run(scenario)

    assert result.passed is True
    assert result.final_status == "completed"
    assert result.order_created == 1


def test_metrics_aggregate():
    metrics = aggregate([
        EvalResult(
            scenario_id="A",
            group="approval",
            title="a",
            passed=True,
            duration_ms=10,
            order_created=1,
        ),
        EvalResult(
            scenario_id="B",
            group="recovery",
            title="b",
            passed=False,
            duration_ms=30,
            order_created=2,
        ),
    ])

    assert metrics.total == 2
    assert metrics.passed == 1
    assert metrics.completion_rate == 0.5
    assert metrics.duplicate_orders == 1
