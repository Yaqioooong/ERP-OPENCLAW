from __future__ import annotations

from datetime import datetime
from typing import Callable

from agent.procurement.config import (
    PROCUREMENT_EXECUTION_LEASE_SECONDS,
    PROCUREMENT_EXECUTION_MAX_ATTEMPTS,
)
from agent.procurement.erp_order_gateway import (
    GatewayBusinessError,
    GatewayTimeout,
    GatewayUnavailable, ErpOrderGateway,
)
from agent.procurement.execution_repository import ExecutionRepository
from agent.procurement.models import (
    ApprovalStatus,
    ExecutionRecord,
    ExecutionStatus,
    OrderExecutionResult,
    OrderVerificationResult,
    ProcurementPlan,
    ToolError,
)
from agent.procurement.ports import ApprovalRepository
from agent.procurement.rules import compute_plan_hash, make_order_number


class SafeOrderExecutionService:
    """带本地执行记录、幂等键、租约和结果核验的订单执行服务。"""

    def __init__(
        self,
        *,
        execution_repository: ExecutionRepository,
        approval_repository: ApprovalRepository,
        gateway: ErpOrderGateway,
        now: Callable[[], datetime],
        lease_seconds: int = PROCUREMENT_EXECUTION_LEASE_SECONDS,
        max_attempts: int = PROCUREMENT_EXECUTION_MAX_ATTEMPTS,
    ):
        self._executions = execution_repository
        self._approvals = approval_repository
        self._gateway = gateway
        self._now = now
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def create_order(
        self,
        *,
        task_id: str,
        plan: ProcurementPlan,
        approval_id: str,
        idempotency_key: str,
    ) -> OrderExecutionResult:
        approval_error = await self._validate_approval(
            task_id=task_id,
            plan=plan,
            approval_id=approval_id,
        )
        if approval_error is not None:
            return approval_error

        existing = await self._executions.get(idempotency_key)
        if existing is not None and existing.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.UNKNOWN,
        }:
            return await self._result_for_existing(existing)

        # PENDING 或租约过期的 EXECUTING 继续走 try_acquire，
        # 否则第一次请求在 ensure_pending 后崩溃时，任务会永远卡在 PENDING。
        plan_hash = compute_plan_hash(plan)
        order_number = make_order_number(
            task_id=task_id,
            plan_version=plan.version,
            plan_hash=plan_hash,
        )
        now = self._now()
        record = ExecutionRecord(
            idempotency_key=idempotency_key,
            task_id=task_id,
            plan_version=plan.version,
            plan_hash=plan_hash,
            approval_id=approval_id,
            status=ExecutionStatus.PENDING,
            plan_snapshot=plan.model_dump(mode="json"),
            order_number=order_number,
            request_payload={
                "task_id": task_id,
                "plan_version": plan.version,
                "plan_hash": plan_hash,
                "approval_id": approval_id,
                "order_number": order_number,
            },
            created_at=now,
            updated_at=now,
        )
        record = await self._executions.ensure_pending(record)

        if record.attempt_count >= self._max_attempts and record.status == ExecutionStatus.FAILED:
            return self._failed_from_record(record)

        acquired = await self._executions.try_acquire(
            idempotency_key=idempotency_key,
            now=now,
            lease_seconds=self._lease_seconds,
        )
        if not acquired:
            current = await self._executions.get(idempotency_key)
            if current is None:
                return self._unknown("EXECUTION_RECORD_MISSING", "执行记录不存在")
            if current.status == ExecutionStatus.SUCCEEDED:
                return self._succeeded_from_record(current)
            if current.status == ExecutionStatus.UNKNOWN:
                return self._unknown(
                    "ORDER_RESULT_UNKNOWN",
                    "之前订单结果不确定，请走 verify/reconcile 核验，不要重复创建。",
                )
            return self._unknown(
                "EXECUTION_IN_PROGRESS",
                "另一个请求正在执行订单，先查询结果，不要重复创建。",
            )

        try:
            order = await self._gateway.create_order(
                order_number=order_number,
                plan=plan,
                idempotency_key=idempotency_key,
            )
        except GatewayTimeout as exc:
            await self._executions.mark_unknown(
                idempotency_key=idempotency_key,
                code="ORDER_RESULT_UNKNOWN",
                message=str(exc),
                now=self._now(),
            )
            return self._unknown(
                "ORDER_RESULT_UNKNOWN",
                f"订单请求超时，结果未知：{exc}",
            )
        except GatewayBusinessError as exc:
            if exc.duplicate:
                return await self._reconcile_after_duplicate(
                    idempotency_key=idempotency_key,
                    order_number=order_number,
                    error=exc,
                )
            await self._executions.mark_failed(
                idempotency_key=idempotency_key,
                code=exc.code,
                message=str(exc),
                now=self._now(),
            )
            return OrderExecutionResult(
                status=ExecutionStatus.FAILED,
                error=ToolError(
                    code=exc.code,
                    retryable=False,
                    message=str(exc),
                ),
            )
        except GatewayUnavailable as exc:
            await self._executions.mark_unknown(
                idempotency_key=idempotency_key,
                code="ERP_UNAVAILABLE",
                message=str(exc),
                now=self._now(),
            )
            return self._unknown(
                "ERP_UNAVAILABLE",
                f"订单服务不可用，结果未知：{exc}",
            )

        await self._executions.mark_succeeded(
            idempotency_key=idempotency_key,
            order_id=order.order_id,
            order_number=order.order_number,
            response=order.raw,
            now=self._now(),
        )
        return OrderExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id=order.order_id,
            order_number=order.order_number,
        )

    async def verify_order(
        self,
        *,
        task_id: str,
        order_id: str | None,
    ) -> OrderVerificationResult:
        record = await self._executions.get_by_task(task_id)
        if record is None:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                order_id=order_id,
                error=ToolError(
                    code="EXECUTION_RECORD_MISSING",
                    retryable=False,
                    message="找不到订单执行记录。",
                ),
            )

        if record.status == ExecutionStatus.SUCCEEDED:
            return OrderVerificationResult(
                status=ExecutionStatus.SUCCEEDED,
                order_id=record.order_id or order_id,
                order_number=record.order_number,
            )

        if record.status == ExecutionStatus.FAILED:
            return OrderVerificationResult(
                status=ExecutionStatus.FAILED,
                order_id=record.order_id or order_id,
                order_number=record.order_number,
                error=ToolError(
                    code=record.last_error_code or "ORDER_EXECUTION_FAILED",
                    retryable=False,
                    message=record.last_error_message or "订单执行失败。",
                ),
            )

        try:
            if order_id:
                order = await self._gateway.get_order_by_id(order_id)
            elif record.order_number:
                order = await self._gateway.get_order_by_number(record.order_number)
            else:
                return OrderVerificationResult(
                    status=ExecutionStatus.UNKNOWN,
                    order_id=order_id,
                    error=ToolError(
                        code="ORDER_LOOKUP_KEY_MISSING",
                        retryable=False,
                        message="没有订单号，无法核验。",
                    ),
                )
        except GatewayUnavailable as exc:
            return OrderVerificationResult(
                status=ExecutionStatus.UNKNOWN,
                order_id=order_id or record.order_id,
                error=ToolError(
                    code="ORDER_VERIFY_UNAVAILABLE",
                    retryable=True,
                    message=str(exc),
                ),
            )

        if order is None:
            if record.order_number:
                await self._executions.mark_failed(
                    idempotency_key=record.idempotency_key,
                    code="ORDER_NOT_FOUND_CONFIRMED",
                    message="远端确认订单未创建。",
                    now=self._now(),
                )
                return OrderVerificationResult(
                    status=ExecutionStatus.FAILED,
                    order_id=order_id or record.order_id,
                    order_number=record.order_number,
                    error=ToolError(
                        code="ORDER_NOT_FOUND_CONFIRMED",
                        retryable=False,
                        message="远端确认订单未创建，不能自动重下。",
                    ),
                )
            return OrderVerificationResult(
                status=ExecutionStatus.UNKNOWN,
                order_id=order_id or record.order_id,
                error=ToolError(
                    code="ORDER_NOT_FOUND_YET",
                    retryable=True,
                    message="远端暂未查到订单，继续核验。",
                ),
            )

        await self._executions.mark_succeeded(
            idempotency_key=record.idempotency_key,
            order_id=order.order_id,
            order_number=order.order_number,
            response=order.raw,
            now=self._now(),
        )
        return OrderVerificationResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id=order.order_id,
            order_number=order.order_number,
        )

    async def _validate_approval(
        self,
        *,
        task_id: str,
        plan: ProcurementPlan,
        approval_id: str,
    ) -> OrderExecutionResult | None:
        approval = await self._approvals.get(approval_id)
        plan_hash = compute_plan_hash(plan)

        if approval is None:
            return self._failed("APPROVAL_NOT_FOUND", "找不到审批记录。")
        if approval.status != ApprovalStatus.APPROVED:
            return self._failed("APPROVAL_NOT_APPROVED", "方案尚未批准。")
        if approval.task_id != task_id:
            return self._failed("APPROVAL_TASK_MISMATCH", "审批记录不属于当前任务。")
        if approval.plan_version != plan.version:
            return self._failed("APPROVAL_PLAN_VERSION_STALE", "审批版本与当前方案不一致。")
        if approval.plan_hash != plan_hash:
            return self._failed("APPROVAL_PLAN_HASH_STALE", "审批方案内容与当前方案不一致。")
        return None

    async def _result_for_existing(
        self,
        record: ExecutionRecord,
    ) -> OrderExecutionResult:
        if record.status == ExecutionStatus.SUCCEEDED:
            return self._succeeded_from_record(record)
        if record.status == ExecutionStatus.UNKNOWN:
            # 已有 UNKNOWN 时不能直接凭“查到订单”判成功；
            # 交给 deeper_4 的 OrderReconciler 做内容核验。
            return self._unknown(
                "ORDER_RESULT_UNKNOWN",
                "之前订单结果不确定，请走 verify/reconcile 核验，不要重复创建。",
            )
        if record.status == ExecutionStatus.FAILED:
            return self._failed_from_record(record)
        if record.status == ExecutionStatus.EXECUTING:
            return self._unknown(
                "EXECUTION_IN_PROGRESS",
                "该订单正在执行，先查询结果，不要重复创建。",
            )
        return self._unknown(
            "EXECUTION_PENDING",
            "该订单已记录执行意图，等待执行。",
        )

    async def _reconcile_after_duplicate(
        self,
        *,
        idempotency_key: str,
        order_number: str,
        error: GatewayBusinessError,
    ) -> OrderExecutionResult:
        try:
            order = await self._gateway.get_order_by_number(order_number)
        except GatewayUnavailable as exc:
            await self._executions.mark_unknown(
                idempotency_key=idempotency_key,
                code="ORDER_DUPLICATE_UNVERIFIED",
                message=f"订单号重复但查询失败：{exc}",
                now=self._now(),
            )
            return self._unknown(
                "ORDER_DUPLICATE_UNVERIFIED",
                f"订单号重复但查询失败：{exc}",
            )

        if order is not None:
            await self._executions.mark_succeeded(
                idempotency_key=idempotency_key,
                order_id=order.order_id,
                order_number=order.order_number,
                response=order.raw,
                now=self._now(),
            )
            return OrderExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                order_id=order.order_id,
                order_number=order.order_number,
            )

        await self._executions.mark_failed(
            idempotency_key=idempotency_key,
            code=error.code,
            message=str(error),
            now=self._now(),
        )
        return OrderExecutionResult(
            status=ExecutionStatus.FAILED,
            error=ToolError(
                code=error.code,
                retryable=False,
                message=str(error),
            ),
        )

    @staticmethod
    def _succeeded_from_record(record: ExecutionRecord) -> OrderExecutionResult:
        return OrderExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            order_id=record.order_id,
            order_number=record.order_number,
        )

    @staticmethod
    def _failed_from_record(record: ExecutionRecord) -> OrderExecutionResult:
        return OrderExecutionResult(
            status=ExecutionStatus.FAILED,
            order_id=record.order_id,
            order_number=record.order_number,
            error=ToolError(
                code=record.last_error_code or "ORDER_EXECUTION_FAILED",
                retryable=False,
                message=record.last_error_message or "订单执行失败。",
            ),
        )

    @staticmethod
    def _unknown(code: str, message: str) -> OrderExecutionResult:
        return OrderExecutionResult(
            status=ExecutionStatus.UNKNOWN,
            error=ToolError(code=code, retryable=True, message=message),
        )

    @staticmethod
    def _failed(code: str, message: str) -> OrderExecutionResult:
        return OrderExecutionResult(
            status=ExecutionStatus.FAILED,
            error=ToolError(code=code, retryable=False, message=message),
        )