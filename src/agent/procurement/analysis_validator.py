from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from agent.procurement.analysis_models import (
    AnalysisIssue,
    AnalysisValidation,
    StructuredAnalysis,
)
from agent.procurement.models import ProcurementPlan
from agent.procurement.rules import compute_plan_hash, q2


class AnalysisValidator:
    """只校验，不修改结构化分析结果。"""

    def validate(
            self,
            *,
            analysis: StructuredAnalysis,
            plan: ProcurementPlan,
            now: datetime,
    ) -> AnalysisValidation:
        errors: list[AnalysisIssue] = []
        warnings: list[AnalysisIssue] = list(analysis.warnings)

        if not analysis.lines:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_LINES_EMPTY",
                    message="分析结果没有明细。",
                    field="lines",
                )
            )

        if analysis.task_id != plan.task_id:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_TASK_MISMATCH",
                    message="分析结果不属于当前任务。",
                    field="task_id",
                )
            )

        if analysis.currency != plan.currency:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_CURRENCY_MISMATCH",
                    message="分析币种与方案币种不一致。",
                    field="currency",
                )
            )

        if analysis.plan_version != plan.version:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_PLAN_VERSION_MISMATCH",
                    message=(
                        f"分析版本 {analysis.plan_version} 与方案版本 "
                        f"{plan.version} 不一致。"
                    ),
                    field="plan_version",
                )
            )

        if analysis.plan_hash != compute_plan_hash(plan):
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_PLAN_HASH_MISMATCH",
                    message="分析结果绑定的方案哈希与当前方案不一致。",
                    field="plan_hash",
                )
            )

        seen_parts: set[int] = set()
        computed_total = Decimal("0")

        for line in analysis.lines:
            if line.part_id in seen_parts:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_DUPLICATE_PART",
                        message=f"物料 {line.part_id} 在分析结果中重复。",
                        field=f"lines.{line.part_id}",
                    )
                )
            seen_parts.add(line.part_id)

            if line.quantity <= 0:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_INVALID_QUANTITY",
                        message=f"物料 {line.part_id} 数量必须大于 0。",
                        field=f"lines.{line.part_id}.quantity",
                    )
                )
            if line.unit_price < 0:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_INVALID_PRICE",
                        message=f"物料 {line.part_id} 单价不能为负。",
                        field=f"lines.{line.part_id}.unit_price",
                    )
                )

            expected_subtotal = q2(
                Decimal(line.quantity) * q2(line.unit_price)
            )
            if q2(line.subtotal) != expected_subtotal:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_SUBTOTAL_MISMATCH",
                        message=(
                            f"物料 {line.part_id} 小计 {line.subtotal} "
                            f"与数量×单价 {expected_subtotal} 不一致。"
                        ),
                        field=f"lines.{line.part_id}.subtotal",
                    )
                )
            computed_total += q2(line.subtotal)

            if line.supplier_id != plan.supplier_id:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_LINE_SUPPLIER_MISMATCH",
                        message=(
                            f"物料 {line.part_id} 的供应商 "
                            f"{line.supplier_id} 与方案供应商 "
                            f"{plan.supplier_id} 不一致。"
                        ),
                        field=f"lines.{line.part_id}.supplier_id",
                    )
                )

            if line.inventory_gap is not None and line.inventory_gap < 0:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_INVENTORY_GAP_NEGATIVE",
                        message=f"物料 {line.part_id} 库存缺口不能为负。",
                        field=f"lines.{line.part_id}.inventory_gap",
                    )
                )
            if line.available_quantity is None:
                warnings.append(
                    AnalysisIssue(
                        code="ANALYSIS_INVENTORY_UNKNOWN",
                        message=f"物料 {line.part_id} 库存未知。",
                        field=f"lines.{line.part_id}.available_quantity",
                        severity="warning",
                    )
                )

        plan_part_ids = {item.part_id for item in plan.items}
        analysis_part_ids = {line.part_id for line in analysis.lines}
        if plan_part_ids != analysis_part_ids:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_PART_SET_MISMATCH",
                    message="分析明细的物料集合与方案不一致。",
                    field="lines",
                )
            )

        computed_total = q2(computed_total)
        if computed_total != q2(analysis.total_cost):
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_TOTAL_MISMATCH",
                    message=(
                        f"分析总价 {analysis.total_cost} 与明细合计 "
                        f"{computed_total} 不一致。"
                    ),
                    field="total_cost",
                )
            )

        if q2(analysis.total_cost) != q2(plan.total_amount):
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_PLAN_TOTAL_MISMATCH",
                    message=(
                        f"分析总价 {analysis.total_cost} 与方案总价 "
                        f"{plan.total_amount} 不一致。"
                    ),
                    field="total_cost",
                )
            )

        if analysis.budget is not None and analysis.total_cost > analysis.budget:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_BUDGET_EXCEEDED",
                    message=(
                        f"分析总价 {analysis.total_cost} 超过预算 "
                        f"{analysis.budget}。"
                    ),
                    field="budget",
                )
            )

        if analysis.budget_delta is not None:
            expected_delta = (
                q2(analysis.budget - analysis.total_cost)
                if analysis.budget is not None
                else None
            )
            if expected_delta is None or q2(analysis.budget_delta) != expected_delta:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_BUDGET_DELTA_MISMATCH",
                        message="预算差额与预算/总价不一致。",
                        field="budget_delta",
                    )
                )

        if analysis.inventory_shortage is not None:
            expected_shortage = sum(
                line.inventory_gap or 0 for line in analysis.lines
            )
            if analysis.inventory_shortage != expected_shortage:
                errors.append(
                    AnalysisIssue(
                        code="ANALYSIS_SHORTAGE_MISMATCH",
                        message=(
                            f"库存缺口 {analysis.inventory_shortage} 与明细合计 "
                            f"{expected_shortage} 不一致。"
                        ),
                        field="inventory_shortage",
                    )
                )

        supplier_ids = {
            item.supplier_id for item in analysis.supplier_comparison
        }
        if plan.supplier_id not in supplier_ids:
            errors.append(
                AnalysisIssue(
                    code="ANALYSIS_PLAN_SUPPLIER_MISSING",
                    message="分析结果的供应商对比中缺少方案供应商。",
                    field="supplier_comparison",
                )
            )

        return AnalysisValidation(
            passed=not errors,
            checked_at=now,
            errors=errors,
            warnings=warnings,
        )
