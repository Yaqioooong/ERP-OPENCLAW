"""订单结果核验与对账。

核验只读：本模块永远不会调用 create_order。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from agent.procurement.config import (
    PROCUREMENT_RECONCILE_CONFIRM_ATTEMPTS,
    PROCUREMENT_RECONCILE_CONFIRM_MIN_SECONDS,
    PROCUREMENT_RECONCILE_LEASE_SECONDS,
    PROCUREMENT_RECONCILE_MAX_ATTEMPTS,
)
from agent.procurement.erp_order_gateway import (
    ErpOrderGateway,
    GatewayBusinessError,
    GatewayOrder,
    GatewayTimeout,
    GatewayUnavailable,
)
from agent.procurement.execution_repository import ExecutionRepository
from agent.procurement.models import (
    ExecutionRecord,
    ExecutionStatus,
    OrderVerificationResult,
    ProcurementPlan,
    RemoteOrder,
    RemoteOrderItem,
    ToolError,
    ValidationIssue,
    VerificationOutcome,
    VerificationResult,
)
from agent.procurement.rules import issue, q2

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationStats:
    scanned: int = 0
    verified: int = 0
    succeeded: int = 0
    failed: int = 0
    needs_review: int = 0
    still_unknown: int = 0
    errors: int = 0


def _pick(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def normalize_remote_order(order: GatewayOrder) -> RemoteOrder:
    raw = order.raw or {}
    details = (
        raw.get("orderDetail")
        or raw.get("orderDetails")
        or raw.get("details")
        or []
    )

    items: list[RemoteOrderItem] = []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            part_id = _to_int(_pick(detail, "partId", "part_id"))
            quantity = _to_int(_pick(detail, "quantity", "qty"))
            if part_id is None or quantity is None:
                continue
            items.append(
                RemoteOrderItem(
                    part_id=part_id,
                    quantity=quantity,
                    unit_price=_to_decimal(
                        _pick(detail, "unitPrice", "unit_price")
                    ),
                    subtotal=_to_decimal(
                        _pick(detail, "subtotal", "subTotal")
                    ),
                )
            )

    supplier = raw.get("supplier") or {}
    supplier_id = _to_int(
        _pick(supplier, "id", "supplierId")
    ) or _to_int(_pick(raw, "supplierId", "supplier_id"))

    return RemoteOrder(
        order_id=order.order_id,
        order_number=order.order_number,
        status=_to_int(_pick(raw, "status", "orderStatus")),
        total_amount=_to_decimal(
            _pick(raw, "totalAmount", "total_amount")
        ),
        supplier_id=supplier_id,
        items=items,
        raw=raw,
    )


def validate_remote_order(
    *,
    remote: RemoteOrder,
    plan: ProcurementPlan,
    expected_order_number: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if remote.order_number != expected_order_number:
        issues.append(
            issue(
                "ORDER_NUMBER_MISMATCH",
                f"远端订单号 {remote.order_number} 与本地 {expected_order_number} 不一致。",
            )
        )

    plan_items = {item.part_id: item for item in plan.items}
    remote_items = {item.part_id: item for item in remote.items}

    if not remote.items:
        issues.append(
            issue("ORDER_ITEMS_EMPTY", "远端订单没有返回明细，不能确认内容。")
        )
        return issues

    for part_id, plan_item in plan_items.items():
        remote_item = remote_items.get(part_id)
        if remote_item is None:
            issues.append(
                issue(
                    "ORDER_ITEM_MISSING",
                    f"远端订单缺少物料 {part_id}。",
                )
            )
            continue

        if remote_item.quantity != plan_item.quantity:
            issues.append(
                issue(
                    "ORDER_QUANTITY_MISMATCH",
                    f"物料 {part_id} 数量：远端 {remote_item.quantity}，"
                    f"本地 {plan_item.quantity}。",
                )
            )

        if remote_item.unit_price is None:
            issues.append(
                issue(
                    "ORDER_PRICE_MISSING",
                    f"物料 {part_id} 远端缺少单价。",
                )
            )
        elif q2(remote_item.unit_price) != q2(plan_item.unit_price):
            issues.append(
                issue(
                    "ORDER_PRICE_MISMATCH",
                    f"物料 {part_id} 单价：远端 {remote_item.unit_price}，"
                    f"本地 {plan_item.unit_price}。",
                )
            )

        expected_subtotal = q2(
            Decimal(plan_item.quantity) * plan_item.unit_price
        )
        if (
            remote_item.subtotal is not None
            and q2(remote_item.subtotal) != expected_subtotal
        ):
            issues.append(
                issue(
                    "ORDER_SUBTOTAL_MISMATCH",
                    f"物料 {part_id} 小计不一致。",
                )
            )

    if remote.total_amount is None:
        issues.append(issue("ORDER_TOTAL_MISSING", "远端订单缺少总金额。"))
    elif q2(remote.total_amount) != q2(plan.total_amount):
        issues.append(
            issue(
                "ORDER_TOTAL_MISMATCH",
                f"总金额：远端 {remote.total_amount}，本地 {plan.total_amount}。",
            )
        )

    if (
        remote.supplier_id is not None
        and plan.supplier_id != remote.supplier_id
    ):
        issues.append(
            issue(
                "ORDER_SUPPLIER_MISMATCH",
                f"供应商：远端 {remote.supplier_id}，本地 {plan.supplier_id}。",
            )
        )

    return issues


class OrderReconciler:
    def __init__(
        self,
        *,
        executions: ExecutionRepository,
        gateway: ErpOrderGateway,
        now: Callable[[], datetime],
        lease_seconds: int = PROCUREMENT_RECONCILE_LEASE_SECONDS,
        max_attempts: int = PROCUREMENT_RECONCILE_MAX_ATTEMPTS,
        confirm_attempts: int = PROCUREMENT_RECONCILE_CONFIRM_ATTEMPTS,
        confirm_min_seconds: int = PROCUREMENT_RECONCILE_CONFIRM_MIN_SECONDS,
    ):
        self._executions = executions
        self._gateway = gateway
        self._now = now
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._confirm_attempts = confirm_attempts
        self._confirm_min_seconds = confirm_min_seconds

    async def verify_by_task(self, task_id: str) -> OrderVerificationResult:
        record = await self._executions.get_by_task(task_id)
        if record is None:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="EXECUTION_RECORD_MISSING",
                    retryable=False,
                    message="找不到订单执行记录。",
                ),
            )
        return self._to_order_verification(await self.verify_record(record))

    async def verify_by_key(self, idempotency_key: str) -> OrderVerificationResult:
        record = await self._executions.get(idempotency_key)
        if record is None:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="EXECUTION_RECORD_MISSING",
                    retryable=False,
                    message="找不到订单执行记录。",
                ),
            )
        return self._to_order_verification(await self.verify_record(record))

    async def verify_record(self, record: ExecutionRecord) -> VerificationResult:
        if record.status == ExecutionStatus.SUCCEEDED:
            return VerificationResult(
                outcome=VerificationOutcome.SUCCEEDED,
                order=RemoteOrder(
                    order_id=record.order_id or "",
                    order_number=record.order_number or "",
                    raw=record.response_payload or {},
                ),
                message="执行记录已成功。",
                attempts=record.verification_attempts,
            )
        if record.status == ExecutionStatus.NEEDS_REVIEW:
            return VerificationResult(
                outcome=VerificationOutcome.MISMATCH,
                message=record.last_error_message or "订单内容需要人工复核。",
                attempts=record.verification_attempts,
            )
        if record.status == ExecutionStatus.FAILED and not record.requires_manual_review:
            return VerificationResult(
                outcome=VerificationOutcome.NOT_CREATED,
                message=record.last_error_message or "订单执行已失败。",
                attempts=record.verification_attempts,
            )

        now = self._now()
        started = time.monotonic()
        logger.info(
            "procurement.reconcile.start task_id=%s key=%s status=%s order_number=%s",
            record.task_id, record.idempotency_key, record.status.value,
            record.order_number,
        )
        acquired = await self._executions.try_acquire_reconcile(
            idempotency_key=record.idempotency_key,
            now=now,
            lease_seconds=self._lease_seconds,
        )
        if not acquired:
            return VerificationResult(
                outcome=VerificationOutcome.UNKNOWN,
                message="另一个核验任务正在处理该订单。",
                attempts=record.verification_attempts,
            )

        try:
            order = await self._lookup(record)
        except (GatewayUnavailable, GatewayTimeout, GatewayBusinessError) as exc:
            await self._executions.mark_still_unknown(
                idempotency_key=record.idempotency_key,
                message=str(exc),
                verification={"outcome": "unavailable", "message": str(exc)},
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.UNKNOWN,
                message=f"订单服务不可用：{exc}",
                attempts=record.verification_attempts + 1,
            )

        if order is None:
            return await self._handle_not_found(record)

        remote = normalize_remote_order(order)

        if remote.status == 5:
            await self._executions.mark_cancelled(
                idempotency_key=record.idempotency_key,
                remote_status=remote.status,
                message="远端订单已取消。",
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.CANCELLED,
                order=remote,
                message="远端订单已取消。",
                attempts=record.verification_attempts + 1,
            )

        plan = self._load_plan(record)
        if plan is None:
            await self._executions.mark_mismatch(
                idempotency_key=record.idempotency_key,
                message="执行记录缺少审批方案快照，无法核验内容。",
                verification={
                    "outcome": "mismatch",
                    "reason": "PLAN_SNAPSHOT_MISSING",
                },
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.MISMATCH,
                order=remote,
                message="执行记录缺少审批方案快照。",
                attempts=record.verification_attempts + 1,
            )

        issues = validate_remote_order(
            remote=remote,
            plan=plan,
            expected_order_number=record.order_number or "",
        )
        if issues:
            payload = {
                "outcome": "mismatch",
                "issues": [item.model_dump(mode="json") for item in issues],
            }
            await self._executions.mark_mismatch(
                idempotency_key=record.idempotency_key,
                message="远端订单内容与已批准方案不一致。",
                verification=payload,
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.MISMATCH,
                order=remote,
                issues=issues,
                message="远端订单内容与已批准方案不一致。",
                attempts=record.verification_attempts + 1,
            )

        await self._executions.mark_verified_succeeded(
            idempotency_key=record.idempotency_key,
            order_id=remote.order_id,
            order_number=remote.order_number,
            remote_status=remote.status,
            response=remote.raw,
            verification={
                "outcome": "succeeded",
                "remote_status": remote.status,
            },
            now=self._now(),
        )
        logger.info(
            "procurement.reconcile.result task_id=%s key=%s outcome=SUCCEEDED "
            "order_id=%s duration_ms=%s",
            record.task_id, record.idempotency_key, remote.order_id,
            int((time.monotonic() - started) * 1000),
        )
        return VerificationResult(
            outcome=VerificationOutcome.SUCCEEDED,
            order=remote,
            message="订单已核验成功。",
            attempts=record.verification_attempts + 1,
        )

    async def reconcile_pending(self, *, limit: int = 50) -> ReconciliationStats:
        stats = ReconciliationStats()
        records = await self._executions.list_needing_reconciliation(
            now=self._now(),
            limit=limit,
        )
        stats.scanned = len(records)

        for record in records:
            try:
                result = await self.verify_record(record)
            except Exception:
                stats.errors += 1
                continue

            stats.verified += 1
            if result.outcome == VerificationOutcome.SUCCEEDED:
                stats.succeeded += 1
            elif result.outcome == VerificationOutcome.MISMATCH:
                stats.needs_review += 1
            elif result.outcome == VerificationOutcome.UNKNOWN:
                stats.still_unknown += 1
            else:
                stats.failed += 1
        return stats

    async def get_execution(self, idempotency_key: str) -> ExecutionRecord | None:
        return await self._executions.get(idempotency_key)

    async def list_pending(self, *, limit: int = 50) -> list[ExecutionRecord]:
        return await self._executions.list_needing_reconciliation(
            now=self._now(),
            limit=limit,
        )

    async def manual_resolve(
        self,
        *,
        idempotency_key: str,
        action: str,
        comment: str,
        order_id: str | None = None,
    ) -> OrderVerificationResult:
        record = await self._executions.get(idempotency_key)
        if record is None:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="EXECUTION_RECORD_MISSING",
                    retryable=False,
                    message="找不到执行记录。",
                ),
            )

        if action == "mark_succeeded":
            if not order_id:
                return OrderVerificationResult(
                    status=ExecutionStatus.FAILED,
                    error=ToolError(
                        code="ORDER_ID_REQUIRED",
                        retryable=False,
                        message="标记成功必须提供 order_id。",
                    ),
                )
            await self._executions.mark_verified_succeeded(
                idempotency_key=idempotency_key,
                order_id=order_id,
                order_number=record.order_number or order_id,
                remote_status=record.remote_status,
                response={"manual": True, "comment": comment},
                verification={"manual": True, "comment": comment},
                now=self._now(),
            )
            return OrderVerificationResult(
                status=ExecutionStatus.SUCCEEDED,
                order_id=order_id,
                order_number=record.order_number or order_id,
            )

        if action == "mark_failed":
            await self._executions.mark_not_created(
                idempotency_key=idempotency_key,
                message=f"人工确认未创建：{comment}",
                now=self._now(),
            )
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="MANUAL_CONFIRMED_NOT_CREATED",
                    retryable=False,
                    message=comment,
                ),
            )

        return OrderVerificationResult(
            status=ExecutionStatus.FAILED,
            error=ToolError(
                code="INVALID_MANUAL_ACTION",
                retryable=False,
                message=f"未知 action: {action}",
            ),
        )

    async def _lookup(self, record: ExecutionRecord) -> GatewayOrder | None:
        if record.order_id:
            return await self._gateway.get_order_by_id(record.order_id)
        if record.order_number:
            return await self._gateway.get_order_by_number(record.order_number)
        raise GatewayUnavailable("执行记录缺少 order_id/order_number，无法核验。")

    async def _handle_not_found(
        self,
        record: ExecutionRecord,
    ) -> VerificationResult:
        attempts = record.verification_attempts + 1
        age_seconds = 0
        if record.created_at:
            age_seconds = int((self._now() - record.created_at).total_seconds())

        if (
            attempts < self._confirm_attempts
            or age_seconds < self._confirm_min_seconds
        ):
            await self._executions.mark_still_unknown(
                idempotency_key=record.idempotency_key,
                message="远端暂未查到订单，继续核验。",
                verification={
                    "outcome": "not_found_yet",
                    "attempts": attempts,
                    "age_seconds": age_seconds,
                },
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.UNKNOWN,
                message="远端暂未查到订单，继续核验。",
                attempts=attempts,
            )

        if attempts >= self._max_attempts:
            await self._executions.mark_mismatch(
                idempotency_key=record.idempotency_key,
                message="核验次数达到上限，订单结果仍需人工确认。",
                verification={
                    "outcome": "needs_review",
                    "attempts": attempts,
                },
                now=self._now(),
            )
            return VerificationResult(
                outcome=VerificationOutcome.MISMATCH,
                message="核验次数达到上限，需人工确认。",
                attempts=attempts,
            )

        await self._executions.mark_not_created(
            idempotency_key=record.idempotency_key,
            message="达到确认窗口后，远端仍无该订单。",
            now=self._now(),
        )
        return VerificationResult(
            outcome=VerificationOutcome.NOT_CREATED,
            message="确认远端未创建订单。",
            attempts=attempts,
        )

    @staticmethod
    def _load_plan(record: ExecutionRecord) -> ProcurementPlan | None:
        if not record.plan_snapshot:
            return None
        try:
            return ProcurementPlan.model_validate(record.plan_snapshot)
        except Exception:
            return None

    @staticmethod
    def _to_order_verification(
        result: VerificationResult,
    ) -> OrderVerificationResult:
        if result.outcome == VerificationOutcome.SUCCEEDED:
            return OrderVerificationResult(
                status=ExecutionStatus.SUCCEEDED,
                order_id=result.order.order_id if result.order else None,
                order_number=result.order.order_number if result.order else None,
            )
        if result.outcome == VerificationOutcome.NOT_CREATED:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code="ORDER_NOT_FOUND_CONFIRMED",
                    retryable=False,
                    message=result.message,
                ),
            )
        if result.outcome == VerificationOutcome.CANCELLED:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                order_id=result.order.order_id if result.order else None,
                order_number=result.order.order_number if result.order else None,
                error=ToolError(
                    code="ORDER_CANCELLED_REMOTE",
                    retryable=False,
                    message=result.message,
                ),
            )
        if result.outcome == VerificationOutcome.MISMATCH:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                order_id=result.order.order_id if result.order else None,
                order_number=result.order.order_number if result.order else None,
                error=ToolError(
                    code="ORDER_CONTENT_MISMATCH",
                    retryable=False,
                    message=result.message,
                ),
                requires_manual_review=True,
            )
        return OrderVerificationResult(
            status=ExecutionStatus.UNKNOWN,
            error=ToolError(
                code="ORDER_RESULT_UNKNOWN",
                retryable=True,
                message=result.message,
            ),
        )
