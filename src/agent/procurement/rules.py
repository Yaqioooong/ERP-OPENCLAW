import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from agent.procurement.config import PROCUREMENT_MAX_QUANTITY, PROCUREMENT_ACTIVE_SUPPLIER_STATUSES
from agent.procurement.models import ValidationIssue, ProcurementRequest, ProcurementPlan, CandidateSet, \
    PlanValidationResult

MONEY = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    # 将一个 Decimal 数值量化（四舍五入）到指定的精度，具体来说通常是保留两位小数、并采用"四舍五入"规则。
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def issue(
        code: str,
        message: str,
        *,
        field: str | None = None,
        severity: str = 'error',
        retryable: bool = False,
        fixable: bool = False,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        field=field,
        message=message,
        severity=severity,
        retryable=retryable,
        fixable=fixable,
    )


# ============================================================
# 1. 需求校验
# ============================================================
def validate_request_fields(
        request: ProcurementRequest, *, today: date
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not request.items:
        issues.append(
            issue(
                "REQUEST_ITEM_MISSING",
                "至少需要一个采购物料（名称、编码或 ID）。",
                field="items",
                fixable=True,
            )
        )
    for index, item in enumerate(request.items):
        prefix = f"items[{index}]"
        if item.part_id is None and not item.part_name and not item.part_code:
            issues.append(
                issue(
                    "REQUEST_PART_MISSING",
                    "需要物料名称、编码或 ID 中的至少一个。",
                    field=f"{prefix}.part",
                    fixable=True,
                )
            )
        if item.quantity is None:
            issues.append(
                issue(
                    "REQUEST_QUANTITY_MISSING",
                    "需要采购数量。",
                    field=f"{prefix}.quantity",
                    fixable=True,
                )
            )
        elif item.quantity <= 0:
            issues.append(
                issue(
                    "REQUEST_QUANTITY_INVALID",
                    "采购数量必须大于 0。",
                    field=f"{prefix}.quantity",
                    fixable=True,
                )
            )
        elif item.quantity > PROCUREMENT_MAX_QUANTITY:
            issues.append(
                issue(
                    "REQUEST_QUANTITY_TOO_LARGE",
                    f"采购数量超过上限 {PROCUREMENT_MAX_QUANTITY}，请拆分或确认。",
                    field=f"{prefix}.quantity",
                    fixable=True,
                )
            )
        if item.target_unit_price is not None and item.target_unit_price <= 0:
            issues.append(
                issue(
                    "REQUEST_TARGET_PRICE_INVALID",
                    "目标单价必须大于 0，或留空。",
                    field=f"{prefix}.target_unit_price",
                    fixable=True,
                )
            )

    if not request.warehouse_location:
        issues.append(
            issue(
                "REQUEST_WAREHOUSE_MISSING",
                "需要收货仓库/库位，例如“华东仓”或“B区-1号库”。",
                field="warehouse_location",
                fixable=True,
            )
        )

    if request.budget is not None and request.budget <= 0:
        issues.append(
            issue(
                "REQUEST_BUDGET_INVALID",
                "预算必须大于 0，或留空。",
                field="budget",
                fixable=True,
            )
        )

    if request.required_by is not None and request.required_by < today:
        issues.append(
            issue(
                "REQUEST_REQUIRED_BY_IN_PAST",
                "要求到货日期不能早于今天。",
                field="required_by",
                fixable=True,
            )
        )

    return issues


# ============================================================
# 2. 方案金额重算
# ============================================================
def recompute_plan_amounts(plan: ProcurementPlan) -> tuple[Decimal, list[ValidationIssue]]:
    """按明细逐项重算小计和总价，不信任方案里自带的金额。"""
    issues: list[ValidationIssue] = []
    total = Decimal("0")

    for index, item in enumerate(plan.items):
        expected_subtotal = q2(Decimal(item.quantity) * item.unit_price)
        if item.subtotal != expected_subtotal:
            issues.append(
                issue(
                    "PLAN_SUBTOTAL_MISMATCH",
                    f"第 {index + 1} 条明细小计应为 {expected_subtotal}，"
                    f"方案给出 {item.subtotal}。",
                    field=f"items[{index}].subtotal",
                    retryable=True,
                )
            )
        total += expected_subtotal
    expected_total = q2(total)
    if plan.total_amount != expected_total:
        issues.append(
            issue(
                "PLAN_TOTAL_MISMATCH",
                f"方案总价应为 {expected_total}，方案给出 {plan.total_amount}。",
                field="total_amount",
                retryable=True,
            )
        )
    return expected_total, issues


# ============================================================
# 3. 方案业务校验
# ============================================================
def validate_plan(
        plan: ProcurementPlan,
        request: ProcurementRequest,
        candidates: CandidateSet,
        *,
        task_id: str,
        today: date,
) -> PlanValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    # 3.1 任务/仓库一致性
    if plan.task_id != task_id:
        errors.append(
            issue(
                "PLAN_TASK_MISMATCH",
                f"方案属于任务 {plan.task_id}，当前任务为 {task_id}。",
                field="task_id",
            )
        )
    if request.warehouse_location and plan.warehouse_location.strip() != request.warehouse_location.strip():
        errors.append(
            issue(
                "PLAN_WAREHOUSE_MISMATCH",
                f"方案仓库为 {plan.warehouse_location}，"
                f"需求仓库为 {request.warehouse_location}。",
                field="warehouse_location",
                retryable=True,
            )
        )

    # 3.2 供应商必须在候选集内，且状态可用
    supplier_offers = [o for o in candidates.offers if o.supplier_id == plan.supplier_id]
    if not supplier_offers:
        errors.append(
            issue(
                "PLAN_SUPPLIER_NOT_IN_CANDIDATES",
                f"供应商 {plan.supplier_id} 不在本次候选集中，禁止使用未查询到的供应商。",
                field="supplier_id",
                retryable=True,
            )
        )
    else:
        statuses = {o.supplier_status for o in supplier_offers}
        if None in statuses:
            warnings.append(
                issue(
                    "SUPPLIER_STATUS_UNKNOWN",
                    "供应商状态未返回，审批时需人工确认。",
                    field="supplier_id",
                    severity="warning",
                )
            )
        unavailable = {
            s for s in statuses if s is not None
                                   and s not in PROCUREMENT_ACTIVE_SUPPLIER_STATUSES
        }
        if unavailable:
            errors.append(
                issue(
                    "SUPPLIER_UNAVAILABLE",
                    f"供应商 {plan.supplier_id} 状态为 {sorted(unavailable)}，不可下单。",
                    field="supplier_id",
                )
            )

        # 3.2.1 用户指定供应商时，方案偏离只给 warning，交由审批人决定
    if (
            request.preferred_supplier_id is not None
            and plan.supplier_id != request.preferred_supplier_id
    ):
        warnings.append(
            issue(
                "PLAN_PREFERRED_SUPPLIER_IGNORED",
                f"用户指定供应商 {request.preferred_supplier_id}，"
                f"方案选择 {plan.supplier_id}，请在审批时确认。",
                field="supplier_id",
                severity="warning",
            )
        )

        # 3.2.2 方案明细数量不能少于需求条目数（多物料映射的细化留到后续）
    if len(plan.items) < len(request.items):
        errors.append(
            issue(
                "PLAN_ITEM_COUNT_MISMATCH",
                f"需求包含 {len(request.items)} 条物料，方案只有 {len(plan.items)} 条。",
                field="items",
                retryable=True,
            )
        )

        # 3.2.3 需求全部条目都给出 part_id 时，方案不能夹带未请求的物料
        # （名称-only 的条目需要先由数据适配器解析成 part_id，混用时不在这里做严格比对）
    if request.items and all(i.part_id is not None for i in request.items):
        requested_ids = {i.part_id for i in request.items}
        for item in plan.items:
            if item.part_id not in requested_ids:
                errors.append(
                    issue(
                        "PLAN_PART_NOT_REQUESTED",
                        f"物料 {item.part_id} 不在本次需求中，禁止加入方案。",
                        field="items",
                        retryable=True,
                    )
                )

        # 3.3 明细：物料、价格必须来自候选数据
    offer_index = {(o.supplier_id, o.part_id): o for o in candidates.offers}
    for index, item in enumerate(plan.items):
        offer = offer_index.get((plan.supplier_id, item.part_id))
        if offer is None:
            errors.append(
                issue(
                    "PLAN_PART_NOT_IN_CANDIDATES",
                    f"第 {index + 1} 条明细的物料 {item.part_id} "
                    f"没有供应商 {plan.supplier_id} 的候选报价。",
                    field=f"items[{index}].part_id",
                    retryable=True,
                )
            )
            continue
        if offer.unit_price is None:
            errors.append(
                issue(
                    "PLAN_PRICE_MISSING",
                    f"物料 {item.part_id} 缺少有效报价，不能编造价格。",
                    field=f"items[{index}].unit_price",
                )
            )
            continue
        if q2(offer.unit_price) != q2(item.unit_price):
            errors.append(
                issue(
                    "PLAN_PRICE_MISMATCH",
                    f"第 {index + 1} 条明细单价应为 {q2(offer.unit_price)}，"
                    f"方案给出 {item.unit_price}。",
                    field=f"items[{index}].unit_price",
                    retryable=True,
                )
            )

    # 3.4 金额重算
    expected_total, amount_issues = recompute_plan_amounts(plan)
    errors.extend(amount_issues)

    # 3.5 预算
    if request.budget is not None and expected_total > request.budget:
        errors.append(
            issue(
                "PLAN_OVER_BUDGET",
                f"方案总价 {expected_total} 超过预算 {request.budget}。",
                field="total_amount",
                retryable=True,
            )
        )

    # 3.6 交期
    if plan.expected_delivery_date is None:
        if request.required_by is not None:
            errors.append(
                issue(
                    "PLAN_DELIVERY_UNKNOWN",
                    "用户指定了要求到货日期，但无法推算交期，不能确认是否满足。",
                    field="expected_delivery_date",
                    retryable=True,
                )
            )
        else:
            warnings.append(
                issue(
                    "PLAN_DELIVERY_DATE_UNKNOWN",
                    "无法从历史数据推算交期，审批时需人工确认。",
                    field="expected_delivery_date",
                    severity="warning",
                )
            )
    elif plan.expected_delivery_date < today:
        errors.append(
            issue(
                "PLAN_DELIVERY_DATE_IN_PAST",
                "方案预计到货日期早于今天。",
                field="expected_delivery_date",
                retryable=True,
            )
        )
    elif request.required_by and plan.expected_delivery_date > request.required_by:
        errors.append(
            issue(
                "PLAN_DELIVERY_TOO_LATE",
                f"方案预计到货 {plan.expected_delivery_date}，"
                f"晚于要求日期 {request.required_by}。",
                field="expected_delivery_date",
                retryable=True,
            )
        )

    # 3.7 库存缺口：未知就标记未知，不把“查不到”当 0
    for gap in plan.inventory_gaps:
        if gap.available_quantity is None:
            warnings.append(
                issue(
                    "INVENTORY_UNKNOWN",
                    f"物料 {gap.part_id} 未查到库存记录，库存缺口未知。",
                    field=f"inventory_gaps.{gap.part_id}",
                    severity="warning",
                )
            )

    plan_hash = compute_plan_hash(plan)
    return PlanValidationResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        plan_hash=plan_hash,
        recomputed_total=expected_total,
    )


# ============================================================
# 4. 方案哈希
# ============================================================
def canonical_plan_payload(plan: ProcurementPlan) -> dict:
    """只包含业务条款，不包含解释性文字/创建时间/库存快照。"""
    return {
        "task_id": plan.task_id,
        "plan_version": plan.version,
        "supplier_id": plan.supplier_id,
        "warehouse_location": plan.warehouse_location.strip(),
        "currency": plan.currency,
        "items": sorted(
            [
                {
                    "part_id": item.part_id,
                    "quantity": item.quantity,
                    "unit_price": format(item.unit_price, ".2f"),
                }
                for item in plan.items
            ],
            key=lambda x: (x["part_id"], x["unit_price"]),
        ),
        "total_amount": format(plan.total_amount, ".2f"),
        "expected_delivery_date": (
            plan.expected_delivery_date.isoformat()
            if plan.expected_delivery_date
            else None
        ),
    }


def compute_plan_hash(plan: ProcurementPlan) -> str:
    payload = json.dumps(
        canonical_plan_payload(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_idempotency_key(task_id: str, plan_version: int, plan_hash: str) -> str:
    raw = f"{task_id}:{plan_version}:{plan_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_approval_id(task_id: str, plan_version: int, plan_hash: str) -> str:
    raw = f"approval:{task_id}:{plan_version}:{plan_hash}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
    return f"APR-{digest}"


# ============================================================
# 5. 执行前守卫
# ============================================================

def execution_guard_errors(state: dict) -> list[str]:
    """在真正调用订单服务前再次校验审批有效性。

    关键：重新从 procurement_plan 计算哈希，不能只信 state 里缓存的 plan_hash。
    """
    errors: list[str] = []
    if state.get("approval_status") != "approved":
        errors.append("APPROVAL_NOT_APPROVED")

    raw_plan = state.get("procurement_plan")
    if not raw_plan:
        errors.append("PLAN_MISSING")
        return errors

    try:
        plan = ProcurementPlan.model_validate(raw_plan)
    except Exception:
        errors.append("PLAN_INVALID")
        return errors

    actual_hash = compute_plan_hash(plan)

    if state.get("plan_hash") != actual_hash:
        errors.append("PLAN_HASH_INCONSISTENT")
    if state.get("plan_version") != plan.version:
        errors.append("PLAN_VERSION_INCONSISTENT")
    if state.get("approval_plan_hash") != actual_hash:
        errors.append("APPROVAL_PLAN_HASH_STALE")
    if state.get("approval_plan_version") != plan.version:
        errors.append("APPROVAL_PLAN_VERSION_STALE")
    if state.get("approved_plan_hash") != actual_hash:
        errors.append("APPROVED_PLAN_HASH_STALE")
    if state.get("approved_plan_version") != plan.version:
        errors.append("APPROVED_PLAN_VERSION_STALE")
    return errors


if __name__ == '__main__':
    print(q2(Decimal("100.459")))  # 100.46
