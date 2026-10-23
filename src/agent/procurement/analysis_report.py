from __future__ import annotations

from typing import Any, Protocol

from agent.procurement.analysis_models import (
    AnalysisValidation,
    StructuredAnalysis,
)


def _money(value) -> str:
    return "" if value is None else f"{value:.2f}"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(
        *,
        analysis: StructuredAnalysis,
        validation: AnalysisValidation,
        narrative: str | None = None,
) -> str:
    lines = [
        "# 采购分析报告",
        "",
        f"- 任务：`{analysis.task_id}`",
        f"- 方案版本：`{analysis.plan_version}`",
        f"- 计算时间：`{analysis.computed_at.isoformat()}`",
        f"- 币种：`{analysis.currency}`",
        "",
        "## 汇总",
        "",
        f"- 总金额：**{_money(analysis.total_cost)}**",
        f"- 预算：{_money(analysis.budget) if analysis.budget is not None else '未提供'}",
        f"- 预算差额：{_money(analysis.budget_delta) if analysis.budget_delta is not None else '未知'}",
        (
            f"- 库存缺口：{analysis.inventory_shortage}"
            if analysis.inventory_shortage is not None
            else "- 库存缺口：未知"
        ),
        "",
        "## 明细",
        "",
        "| 物料 | 名称 | 数量 | 单价 | 小计 | 库存 | 缺口 | 交期 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for line in analysis.lines:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(line.part_id),
                    _cell(line.part_name),
                    str(line.quantity),
                    _money(line.unit_price),
                    _money(line.subtotal),
                    (
                        str(line.available_quantity)
                        if line.available_quantity is not None
                        else "未知"
                    ),
                    (
                        str(line.inventory_gap)
                        if line.inventory_gap is not None
                        else "未知"
                    ),
                    (
                        str(line.lead_time_days)
                        if line.lead_time_days is not None
                        else "未知"
                    ),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## 供应商对比",
        "",
        "| 供应商 | 单价 | 交期 | 可用量 | 状态 | 信用 | 价格来源 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for offer in analysis.supplier_comparison:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{offer.supplier_id} / {_cell(offer.supplier_name)}",
                    _money(offer.unit_price),
                    (
                        str(offer.lead_time_days)
                        if offer.lead_time_days is not None
                        else "未知"
                    ),
                    (
                        str(offer.available_quantity)
                        if offer.available_quantity is not None
                        else "未知"
                    ),
                    (
                        str(offer.supplier_status)
                        if offer.supplier_status is not None
                        else "未知"
                    ),
                    _cell(offer.credit_rating),
                    _cell(offer.price_source),
                ]
            )
            + " |"
        )

    lines += ["", "## 数据来源", ""]
    for source in analysis.sources:
        lines.append(
            f"- `{source.source}`：{source.record_count} 条，"
            f"查询时间 `{source.queried_at.isoformat()}`"
        )
        if source.missing_fields:
            lines.append(
                "  - 缺失字段：" + ", ".join(source.missing_fields)
            )

    lines += ["", "## 校验", ""]
    lines.append(f"- 通过：`{validation.passed}`")
    if validation.errors:
        lines.append("- 错误：")
        for issue in validation.errors:
            lines.append(f"  - `{issue.code}` {issue.message}")
    if validation.warnings:
        lines.append("- 警告：")
        for issue in validation.warnings:
            lines.append(f"  - `{issue.code}` {issue.message}")

    if narrative:
        lines += ["", "## 分析说明", "", narrative]

    lines += [
        "",
        "> 本报告中的金额、数量、库存缺口由确定性计算生成；"
        "模型说明文字不参与数值计算。",
        "",
    ]
    return "\n".join(lines)


class ReportNarrator(Protocol):
    async def narrate(
            self,
            *,
            analysis: StructuredAnalysis,
            validation: AnalysisValidation,
    ) -> str: ...


class LlmReportNarrator:
    """只生成解释文字；数值表格由 render_report 生成。"""

    def __init__(self, model: Any):
        self._model = model

    async def narrate(
            self,
            *,
            analysis: StructuredAnalysis,
            validation: AnalysisValidation,
    ) -> str:
        payload = analysis.model_dump(mode="json")
        prompt = (
            "你是采购分析报告的解释者。下面 JSON 已经由程序计算并校验。\n"
            "要求：\n"
            "1. 只能解释 JSON 中的事实，不得修改、补造任何数字；\n"
            "2. 如果 validation.passed=false，明确说明不能据此下单；\n"
            "3. 用 3-6 句话说明总价、库存缺口、供应商选择和主要风险；\n"
            "4. 不要输出 Markdown 表格，不要说“根据我的计算”。\n\n"
            f"JSON:\n{payload}\n\n"
            f"validation:\n{validation.model_dump(mode='json')}"
        )
        response = await self._model.ainvoke(prompt)
        content = getattr(response, "content", response)
        return str(content).strip()
