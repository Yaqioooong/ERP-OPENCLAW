from __future__ import annotations

from statistics import mean

from pydantic import BaseModel, Field

from evals.models import EvalResult


class EvalMetrics(BaseModel):
    total: int = 0
    passed: int = 0
    completion_rate: float = 0.0
    rule_pass_rate: float = 0.0
    recovery_rate: float = 0.0
    duplicate_orders: int = 0
    avg_tool_calls: float = 0.0
    p95_duration_ms: int = 0
    groups: dict[str, dict[str, int]] = Field(default_factory=dict)


def aggregate(results: list[EvalResult]) -> EvalMetrics:
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    durations = sorted(result.duration_ms for result in results)
    p95 = (
        durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        if durations else 0
    )
    groups: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = groups.setdefault(
            result.group, {"total": 0, "passed": 0}
        )
        bucket["total"] += 1
        bucket["passed"] += int(result.passed)

    return EvalMetrics(
        total=total,
        passed=passed,
        completion_rate=passed / total if total else 0.0,
        rule_pass_rate=(
            sum(1 for r in results if not r.failures) / total if total else 0.0
        ),
        recovery_rate=(
            sum(1 for r in results if r.group == "recovery" and r.passed)
            / max(1, sum(1 for r in results if r.group == "recovery"))
        ),
        duplicate_orders=sum(
            max(0, r.order_created - 1) for r in results
        ),
        avg_tool_calls=mean([r.tool_calls for r in results]) if results else 0.0,
        p95_duration_ms=p95,
        groups=groups,
    )
