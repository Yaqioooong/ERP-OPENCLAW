from __future__ import annotations

import asyncio
import logging

from agent.procurement.config import (
    PROCUREMENT_RECONCILE_BATCH_SIZE,
    PROCUREMENT_RECONCILE_INTERVAL_SECONDS,
)
from agent.procurement.order_reconciliation import OrderReconciler

logger = logging.getLogger(__name__)


class ReconciliationWorker:
    def __init__(
        self,
        *,
        reconciler: OrderReconciler,
        interval_seconds: int = PROCUREMENT_RECONCILE_INTERVAL_SECONDS,
        batch_size: int = PROCUREMENT_RECONCILE_BATCH_SIZE,
    ):
        self._reconciler = reconciler
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(), name="procurement-reconciler"
            )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    async def run_once(self):
        return await self._reconciler.reconcile_pending(
            limit=self._batch_size
        )

    async def _run(self) -> None:
        logger.info(
            "采购订单对账 worker 启动：interval=%ss batch=%s",
            self._interval,
            self._batch_size,
        )
        while not self._stop.is_set():
            try:
                stats = await self.run_once()
                if stats.scanned:
                    logger.info(
                        "采购订单对账：scanned=%s succeeded=%s failed=%s "
                        "needs_review=%s unknown=%s errors=%s",
                        stats.scanned,
                        stats.succeeded,
                        stats.failed,
                        stats.needs_review,
                        stats.still_unknown,
                        stats.errors,
                    )
            except Exception:
                logger.exception("采购订单对账 worker 异常")

            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
            except asyncio.TimeoutError:
                continue
