from __future__ import annotations

from datetime import datetime

from evals.metrics import EvalMetrics
from evals.models import EvalResult


def render_markdown(
    *,
    results: list[EvalResult],
    metrics: EvalMetrics,
    started_at: datetime,
) -> str:
    lines = [
        "# 采购 Agent 评测报告",
        "",
        f"- 生成时间：`{datetime.now().astimezone().isoformat()}`",
        f"- 开始时间：`{started_at.isoformat()}`",
        f"- 场景数：{metrics.total}",
        f"- 通过数：{metrics.passed}",
        f"- 完成率：{metrics.completion_rate:.1%}",
        f"- 规则通过率：{metrics.rule_pass_rate:.1%}",
        f"- 恢复成功率：{metrics.recovery_rate:.1%}",
        f"- 重复订单数：{metrics.duplicate_orders}",
        f"- 平均工具调用：{metrics.avg_tool_calls:.2f}",
        f"- P95 耗时：{metrics.p95_duration_ms} ms",
        "",
        "## 分组结果",
        "",
        "| group | passed | total |",
        "|---|---:|---:|",
    ]
    for group, bucket in sorted(metrics.groups.items()):
        lines.append(
            f"| {group} | {bucket['passed']} | {bucket['total']} |"
        )

    lines += [
        "",
        "## 场景明细",
        "",
        "| ID | 标题 | 结果 | 状态 | 错误 | 订单数 | 工具调用 | 耗时 |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for result in results:
        lines.append(
            "| "
            + " | ".join(
                [
                    result.scenario_id,
                    result.title,
                    "PASS" if result.passed else "FAIL",
                    result.final_status or "-",
                    result.error_code or "-",
                    str(result.order_created),
                    str(result.tool_calls),
                    f"{result.duration_ms}ms",
                ]
            )
            + " |"
        )

    failed = [result for result in results if not result.passed]
    if failed:
        lines += ["", "## 失败详情", ""]
        for result in failed:
            lines.append(f"### {result.scenario_id} {result.title}")
            lines.append("")
            for failure in result.failures:
                lines.append(f"- {failure}")
            lines.append("")

    return "\n".join(lines)
