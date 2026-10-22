from agent.procurement.models import PlanProposal, PlanProposalItem
from agent.procurement.rules import q2


class DeterministicPlanProposer:
    """在候选供应商中做确定性打分，不依赖模型。"""
    _WEIGHTS = {
        "price": (0.7, 0.2, 0.1),
        "lead_time": (0.2, 0.7, 0.1),
        "balanced": (0.4, 0.4, 0.2),
    }
    _CREDIT_RISK = {"A": 0.0, "B": 0.3, "C": 0.6, "D": 1.0}

    async def propose(self, request, candidates, *, feedback):
        # 1. 每个需求条目都必须先解析到 part_id;否则让上游追问，不猜
        requested: list[tuple[int, int]] = []
        for item in request.items:
            if item.part_id is None or item.quantity is None:
                raise ValueError(
                    "需求物料尚未解析到part_id/quantity, 不能生成方案"
                )
            requested.append((item.part_id, item.quantity))
        # 2. 按供应商聚合，要求该供应商覆盖全部需求条目且价格已知
        by_supplier: dict[int, dict[int, object]] = {}
        for offer in candidates.offers:
            by_supplier.setdefault(offer.supplier_id, {})[offer.part_id] = offer

        complete: list[dict] = []
        for supplier_id, offers in by_supplier.items():
            if any(part_id not in offers for part_id, _ in requested):
                continue
            chosen = [offers[part_id] for part_id, _ in requested]
            if any(o.unit_price is None for o in offers):
                continue
            total_price = sum(
                q2(o.unit_price) * qty for o, (_, qty) in zip(chosen, requested)
            )
            lead_times = [o.lead_time_days for o in chosen]
            max_lead = max((x for x in lead_times if x is not None), default=None)
            credit = chosen[0].credit_rating or 'B'
            complete.append(
                {
                    "supplier_id": supplier_id,
                    "offers": chosen,
                    "total_price": total_price,
                    "lead_time": max_lead,
                    "risk": self._CREDIT_RISK.get(credit, 0.5),
                }
            )
        if not complete:
            raise ValueError("NO_COMPLETE_SUPPLIER：没有供应商能覆盖全部需求并给出有效报价。")

        # 3. 归一化后加权打分；缺失交期按最差处理，但不伪造具体天数
        prices = [c["total_price"] for c in complete]
        leads = [c["lead_time"] for c in complete if c["lead_time"] is not None]
        price_min, price_max = min(prices), max(prices)
        lead_min, lead_max = (min(leads), max(leads)) if leads else (0, 0)

        def norm(value, low, high):
            if high <= low:
                return 0.0
            return (float(value) - float(low)) / (float(high) - float(low))

        w_price, w_lead, w_risk = self._WEIGHTS.get(
            request.priority, self._WEIGHTS["balanced"]
        )
        for c in complete:
            lead_value = (
                c["lead_time"] if c["lead_time"] is not None else lead_max + 1
            )
            c["score"] = (
                    w_price * norm(c["total_price"], price_min, price_max)
                    + w_lead * norm(lead_value, lead_min, lead_max + 1)
                    + w_risk * c["risk"]
            )
        best = min(complete, key=lambda c: (c["score"], c["total_price"]))
        return PlanProposal(
            supplier_id=best["supplier_id"],
            items=[
                PlanProposalItem(part_id=part_id, quantity=qty)
                for part_id, qty in requested
            ],
            rationale=(
                f"在 {len(complete)} 个可覆盖全部需求的供应商中，"
                f"按 {request.priority} 偏好选择供应商 {best['supplier_id']}；"
                f"预估总价 {q2(best['total_price'])}。"
            ),
            confidence=max(0.0, min(1.0, 1.0 - float(best["score"]))),
        )
