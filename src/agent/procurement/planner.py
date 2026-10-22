"""负责把“模型/启发式选出的供应商 + 物料 + 数量”变成完整方案。金额、交期、库存缺口都由它计算，模型不直接写这些字段"""
from datetime import datetime, timedelta
from decimal import Decimal

from agent.procurement.models import ProcurementRequest, CandidateSet, PlanProposal, ProcurementPlan, PlanItem, \
    InventoryGap
from agent.procurement.rules import q2


class DefaultPlanBuilder:
    def build(
            self,
            *,
            task_id: str,
            version: int,
            request: ProcurementRequest,
            candidates: CandidateSet,
            proposal: PlanProposal,
            now: datetime,
    ) -> ProcurementPlan:
        offer_index = {
            (o.supplier_id, o.part_id): o for o in candidates.offers
        }
        items: list[PlanItem] = []
        gaps: list[InventoryGap] = []
        lead_times: list[int] = []

        for proposal_item in proposal.items:
            offer = offer_index.get((proposal.supplier_id, proposal_item.part_id))
            if offer is None or offer.unit_price is None:
                raise ValueError(
                    f"供应商 {proposal.supplier_id} 对物料 "
                    f"{proposal_item.part_id} 没有有效报价，不能生成方案。"
                )
            unit_price = q2(offer.unit_price)
            subtotal = q2(Decimal(proposal_item.quantity) * unit_price)
            items.append(PlanItem(
                part_id=offer.part_id,
                part_name=offer.part_name,
                quantity=proposal_item.quantity,
                unit_price=unit_price,
                subtotal=subtotal,
                price_source=offer.price_source or "part_master",
                lead_time_days=offer.lead_time_days,
            ))
            if offer.lead_time_days is not None:
                lead_times.append(offer.lead_time_days)
            snapshot = next(
                (
                    s for s in candidates.inventory
                    if s.part_id == offer.part_id
                       and (
                               not request.warehouse_location or s.warehouse_location == request.warehouse_location
                       )
                ),
                None,
            )
            available = snapshot.current_quantity if snapshot else None
            gaps.append(InventoryGap(
                part_id=offer.part_id,
                requested_quantity=proposal_item.quantity,
                available_quantity=available,
                gap=(
                    max(0, proposal_item.quantity - available)
                    if available is not None
                    else None
                ),
            ))
        if not items:
            raise ValueError("候选方案为空，不能生成采购方案。")

        total = q2(sum((item.subtotal for item in items), Decimal("0")))
        supplier_name = next(
            (
                o.supplier_name
                for o in candidates.offers
                if o.supplier_id == proposal.supplier_id
            ),
            f"SUP-{proposal.supplier_id}",
        )
        expected_delivery_date = (
            now.date() + timedelta(days=max(lead_times))
            if len(lead_times) == len(items)
            else None
        )

        return ProcurementPlan(
            plan_id=f"PLAN-{task_id}-V{version}",
            task_id=task_id,
            version=version,
            supplier_id=proposal.supplier_id,
            supplier_name=supplier_name,
            warehouse_location=request.warehouse_location or "",
            items=items,
            total_amount=total,
            currency="CNY",
            expected_delivery_date=expected_delivery_date,
            inventory_gaps=gaps,
            budget=request.budget,
            rationale=proposal.rationale,
            created_at=now,
        )
