"""把订单对账/人工复核结果回写到采购任务。

对账模块只负责“订单执行事实”，本模块负责“这笔事实对任务意味着什么”：
成功 → completed，内容不一致/远端取消 → awaiting_review，确认未创建 →
not_created（非终态，可人工回转）。只有任务实际发生变化时才发事件。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from agent.procurement.config import utc_now_in_business_tz
from agent.procurement.models import (
    ExecutionRecord,
    ExecutionStatus,
    TaskStatus,
    VerificationOutcome,
    VerificationResult,
)
from agent.procurement.task_events import TaskEventService, TaskEventType
from agent.procurement.task_repository import TaskRepository

logger = logging.getLogger(__name__)


class TaskReconciliationService:
    def __init__(
        self,
        *,
        tasks: TaskRepository,
        events: TaskEventService | None = None,
        now: Callable[[], datetime] = utc_now_in_business_tz,
    ):
        self._tasks = tasks
        self._events = events
        self._now = now

    async def apply_verification(
        self,
        execution: ExecutionRecord,
        result: VerificationResult,
    ) -> bool:
        """把一次对账结果映射为任务状态并回写。"""
        mapping = self._map_verification(execution, result)
        if mapping is None:
            return False
        status, order_id, requires_manual_review, code, message = mapping
        return await self._apply(
            execution,
            status=status,
            order_id=order_id,
            requires_manual_review=requires_manual_review,
            code=code,
            message=message,
        )

    async def apply_execution_record(self, execution: ExecutionRecord) -> bool:
        """人工 resolve 后，按执行记录当前状态回写任务。"""
        mapping = self._map_execution(execution)
        if mapping is None:
            return False
        status, order_id, requires_manual_review, code, message = mapping
        return await self._apply(
            execution,
            status=status,
            order_id=order_id,
            requires_manual_review=requires_manual_review,
            code=code,
            message=message,
        )

    async def _apply(
        self,
        execution: ExecutionRecord,
        *,
        status: str,
        order_id: str | None,
        requires_manual_review: bool,
        code: str | None,
        message: str | None,
    ) -> bool:
        record = await self._tasks.get(execution.task_id)
        if record is None:
            logger.warning(
                "procurement.task.reconcile.missing_task task_id=%s key=%s",
                execution.task_id,
                execution.idempotency_key,
            )
            return False

        # completed 是更强的事实：除 completed 外不允许被后续对账降级。
        if (
            record.status == TaskStatus.COMPLETED.value
            and status != TaskStatus.COMPLETED.value
        ):
            return False

        # 幂等：状态/订单号/复核标记/错误码都没变化时不再写库、不再发事件。
        if (
            record.status == status
            and record.order_id == order_id
            and record.requires_manual_review == requires_manual_review
            and record.last_error_code == code
        ):
            return False

        error = {"code": code, "message": message} if code else None
        changed = await self._tasks.apply_reconciliation(
            task_id=execution.task_id,
            status=status,
            order_id=order_id,
            requires_manual_review=requires_manual_review,
            execution_key=execution.idempotency_key,
            error=error,
            now=self._now(),
        )
        if not changed:
            return False

        logger.info(
            "procurement.task.reconciled task_id=%s key=%s status=%s "
            "order_id=%s manual_review=%s code=%s",
            execution.task_id,
            execution.idempotency_key,
            status,
            order_id,
            requires_manual_review,
            code,
        )
        if self._events is not None:
            try:
                await self._events.emit(
                    task_id=execution.task_id,
                    event_type=TaskEventType.TASK_RECONCILED,
                    data={
                        "status": status,
                        "order_id": order_id,
                        "requires_manual_review": requires_manual_review,
                        "code": code,
                        "execution_key": execution.idempotency_key,
                    },
                )
            except Exception:
                logger.exception(
                    "采购任务对账事件写入失败 task_id=%s", execution.task_id
                )
        return True

    def _map_verification(
        self,
        execution: ExecutionRecord,
        result: VerificationResult,
    ) -> tuple[str, str | None, bool, str | None, str | None] | None:
        order_id = (
            result.order.order_id
            if result.order is not None and result.order.order_id
            else execution.order_id
        )
        if result.outcome == VerificationOutcome.SUCCEEDED:
            return (
                TaskStatus.COMPLETED.value,
                order_id,
                False,
                None,
                None,
            )
        if result.outcome == VerificationOutcome.NOT_CREATED:
            return (
                TaskStatus.NOT_CREATED.value,
                None,
                False,
                "ORDER_NOT_FOUND_CONFIRMED",
                result.message or "确认远端未创建订单。",
            )
        if result.outcome == VerificationOutcome.MISMATCH:
            return (
                TaskStatus.AWAITING_REVIEW.value,
                order_id,
                True,
                "ORDER_CONTENT_MISMATCH",
                result.message or "远端订单内容与方案不一致。",
            )
        if result.outcome == VerificationOutcome.CANCELLED:
            return (
                TaskStatus.AWAITING_REVIEW.value,
                order_id,
                True,
                "ORDER_CANCELLED_REMOTE",
                result.message or "远端订单已取消。",
            )
        if result.outcome == VerificationOutcome.UNAVAILABLE:
            # 核验通道不可用只说明“暂时查不了”，不能据此改变任务状态。
            return None
        # UNKNOWN：保持任务现状，等待下一次对账。
        return None

    def _map_execution(
        self,
        execution: ExecutionRecord,
    ) -> tuple[str, str | None, bool, str | None, str | None] | None:
        if execution.status == ExecutionStatus.SUCCEEDED:
            return (
                TaskStatus.COMPLETED.value,
                execution.order_id,
                False,
                None,
                None,
            )
        if execution.status == ExecutionStatus.NEEDS_REVIEW:
            return (
                TaskStatus.AWAITING_REVIEW.value,
                execution.order_id,
                True,
                execution.last_error_code or "ORDER_CONTENT_MISMATCH",
                execution.last_error_message or "订单内容需要人工复核。",
            )
        if execution.status == ExecutionStatus.FAILED:
            code = execution.last_error_code or "ORDER_EXECUTION_FAILED"
            if code in {
                "ORDER_NOT_FOUND_CONFIRMED",
                "MANUAL_CONFIRMED_NOT_CREATED",
            }:
                return (
                    TaskStatus.NOT_CREATED.value,
                    None,
                    False,
                    code,
                    execution.last_error_message or "人工确认未创建订单。",
                )
            if code == "ORDER_CANCELLED_REMOTE":
                return (
                    TaskStatus.AWAITING_REVIEW.value,
                    execution.order_id,
                    True,
                    code,
                    execution.last_error_message or "远端订单已取消。",
                )
        return None


__all__ = ["TaskReconciliationService"]
