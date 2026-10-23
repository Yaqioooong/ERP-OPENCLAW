from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from evals.metrics import aggregate
from evals.report import render_markdown
from evals.runner import EvalRunner
from evals.scenarios import SCENARIOS


async def main_async(output: Path, scenario_id: str | None) -> None:
    started_at = datetime.now().astimezone()
    scenarios = [
        scenario for scenario in SCENARIOS
        if scenario_id is None or scenario.id == scenario_id
    ]
    runner = EvalRunner()
    results = []
    for scenario in scenarios:
        print(f"[eval] {scenario.id} {scenario.title}")
        try:
            results.append(await runner.run(scenario))
        except Exception as exc:
            from evals.models import EvalResult

            results.append(
                EvalResult(
                    scenario_id=scenario.id,
                    group=scenario.group,
                    title=scenario.title,
                    passed=False,
                    duration_ms=0,
                    failures=[f"runner exception: {type(exc).__name__}: {exc}"],
                )
            )

    metrics = aggregate(results)
    report = render_markdown(
        results=results, metrics=metrics, started_at=started_at
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    output.with_suffix(".json").write_text(
        json.dumps(
            {
                "metrics": metrics.model_dump(mode="json"),
                "results": [r.model_dump(mode="json") for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"report: {output}")
    print(
        f"passed {metrics.passed}/{metrics.total} "
        f"({metrics.completion_rate:.1%})"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=f"reports/eval_{datetime.now():%Y%m%d_%H%M%S}.md",
    )
    parser.add_argument("--scenario", default=None)
    args = parser.parse_args()
    asyncio.run(main_async(Path(args.output), args.scenario))


if __name__ == "__main__":
    main()
