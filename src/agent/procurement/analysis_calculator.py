from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from agent.procurement.analysis_models import (
    AnalysisIssue,
    AnalysisLine,
    DataSourceInfo,
    StructuredAnalysis,
    SupplierComparison,
)
from agent.procurement.models import (
    CandidateSet,
    InventorySnapshot,
    ProcurementPlan,
    ProcurementRequest,
    SupplierOffer,
)
from agent.procurement.rules import compute_plan_hash, q2


class AnalysisInvariantError(ValueError):
    """确定性计算发现不可继续的硬错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _find_inventory(
        candidates: CandidateSet,
        *,
        part_id: int,
        warehouse_location: str | None,
) -> InventorySnapshot | None:
    exact = [
        item for item in candidates.inventory
        if item.part_id == part_id
           and (
                   not warehouse_location
                   or item.warehouse_location == warehouse_location
           )
    ]
    if exact:
        return exact[0]
    if warehouse_location:
        # 指定仓库时不能用其他仓库的库存冒充可用库存。
        return None
    return next(
        (item for item in candidates.inventory if item.part_id == part_id),
        None,
    )


def _offer_to_comparison(offer: SupplierOffer) -> SupplierComparison:
    warnings: list[str] = []
    if offer.supplier_status is None:
        warnings.append("SUPPLIER_STATUS_UNKNOWN")
    if offer.unit_price is None:
        warnings.append("UNIT_PRICE_UNKNOWN")
    return SupplierComparison(
        supplier_id=offer.supplier_id,
        supplier_name=offer.supplier_name,
        unit_price=offer.unit_price,
        lead_time_days=offer.lead_time_days,
        available_quantity=offer.available_quantity,
        supplier_status=offer.supplier_status,
        credit_rating=offer.credit_rating,
        price_source=offer.price_source,
        warnings=warnings,
    )


class DeterministicAnalyzer:
    """所有金额/缺口只在本类中计算；输入是已查询的数据。"""

    def analyze(
            self,
            *,
            task_id: str,
            request: ProcurementRequest,
            candidates: CandidateSet,
            plan: ProcurementPlan,
            now: datetime,
            sources: list[DataSourceInfo] | None = None,
    ) -> StructuredAnalysis:
        if not plan.items:
            raise AnalysisInvariantError(
                "PLAN_ITEMS_EMPTY", "采购方案没有明细，不能分析。"
            )

        offer_index: dict[tuple[int, int], SupplierOffer] = {
            (offer.supplier_id, offer.part_id): offer
            for offer in candidates.offers
        }

        lines: list[AnalysisLine] = []
        unknown_inventory: list[int] = []
        shortage_total = 0
        shortage_known = True

        for item in plan.items:
            offer = offer_index.get((plan.supplier_id, item.part_id))
            if offer is None:
                raise AnalysisInvariantError(
                    "OFFER_MISSING",
                    f"供应商 {plan.supplier_id} 对物料 {item.part_id} 没有报价。",
                )
            if offer.unit_price is None:
                raise AnalysisInvariantError(
                    "UNIT_PRICE_MISSING",
                    f"物料 {item.part_id} 没有有效单价。",
                )

            computed_subtotal = q2(
                Decimal(item.quantity) * q2(offer.unit_price)
            )
            if q2(item.subtotal) != computed_subtotal:
                raise AnalysisInvariantError(
                    "LINE_SUBTOTAL_MISMATCH",
                    f"物料 {item.part_id} 明细小计不一致："
                    f"plan={item.subtotal}, computed={computed_subtotal}。",
                )

            snapshot = _find_inventory(
                candidates,
                part_id=item.part_id,
                warehouse_location=request.warehouse_location,
            )
            available = (
                snapshot.current_quantity if snapshot is not None else None
            )
            if available is None:
                inventory_gap = None
                shortage_known = False
                unknown_inventory.append(item.part_id)
            else:
                inventory_gap = max(0, item.quantity - available)
                shortage_total += inventory_gap

            lines.append(
                AnalysisLine(
                    part_id=item.part_id,
                    part_name=item.part_name,
                    quantity=item.quantity,
                    unit_price=q2(offer.unit_price),
                    subtotal=computed_subtotal,
                    supplier_id=plan.supplier_id,
                    lead_time_days=offer.lead_time_days,
                    available_quantity=available,
                    inventory_gap=inventory_gap,
                )
            )

        total_cost = q2(
            sum((line.subtotal for line in lines), Decimal("0"))
        )
        if total_cost != q2(plan.total_amount):
            raise AnalysisInvariantError(
                "PLAN_TOTAL_MISMATCH",
                f"明细合计 {total_cost} 与方案总价 {plan.total_amount} 不一致。",
            )

        budget_delta = (
            q2(plan.budget - total_cost)
            if plan.budget is not None
            else None
        )

        warnings: list[AnalysisIssue] = [
            AnalysisIssue(
                code="DATA_WARNING",
                message=warning,
                severity="warning",
            )
            for warning in candidates.data_warnings
        ]
        for part_id in unknown_inventory:
            warnings.append(
                AnalysisIssue(
                    code="INVENTORY_UNKNOWN",
                    message=f"物料 {part_id} 没有可用库存记录，缺口未知。",
                    field=f"inventory.{part_id}",
                    severity="warning",
                )
            )

        supplier_comparison = [
            _offer_to_comparison(offer)
            for offer in candidates.offers
        ]

        return StructuredAnalysis(
            task_id=task_id,
            plan_version=plan.version,
            plan_hash=compute_plan_hash(plan),
            computed_at=now,
            currency=plan.currency,
            budget=plan.budget,
            total_cost=total_cost,
            budget_delta=budget_delta,
            inventory_shortage=(
                shortage_total if shortage_known else None
            ),
            unknown_inventory_parts=unknown_inventory,
            lines=lines,
            supplier_comparison=supplier_comparison,
            sources=sources or [],
            warnings=warnings,
        )
