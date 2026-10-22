"""采购任务的启动/恢复/查询/对账服务。

Checkpoint 负责“图执行到哪一步”，本模块负责“这笔任务属于谁、现在能不能恢复”。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from langgraph.types import Command

from agent.procurement import initial_state
from agent.procurement.config import PROCUREMENT_TIMEZONE, utc_now_in_business_tz
from agent.procurement.models import TaskRecord, TaskRunResult, TaskStatus
from agent.procurement.task_repository import TaskRepository
from agent.schema import ProcurementContext


def new_task_id(now: datetime | None = None) -> str:
    current = now or datetime.now(ZoneInfo(PROCUREMENT_TIMEZONE))
    suffix = uuid4().hex[:8].upper()
    return f"TASK-{current:%Y%m%d}-{suffix}"


def task_thread_id(task_id: str) -> str:
    return f"procurement:{task_id}"


# 兼容之前写错的命名；新代码统一使用 task_thread_id。
task_thread_iid = task_thread_id


class TaskCheckpointMissing(RuntimeError):
    pass


class ProcurementTaskRunner:
    def __init__(
        self,
        *,
        graph,
        task_repository: TaskRepository,
        now: Callable[[], datetime] = utc_now_in_business_tz,
    ):
        self._graph = graph
        self._tasks = task_repository
        self._now = now

    async def start(
        self,
        *,
        raw_request: str,
        user_id: str,
        username: str,
        conversation_id: str | None = None,
        request: dict | None = None,
    ) -> TaskRunResult:
        now = self._now()
        task_id = new_task_id(now)
        thread_id = task_thread_id(task_id)

        await self._tasks.create(
            TaskRecord(
                task_id=task_id,
                thread_id=thread_id,
                conversation_id=conversation_id,
                user_id=user_id,
                status=TaskStatus.RUNNING.value,
                created_at=now,
                updated_at=now,
            )
        )

        config = {"configurable": {"thread_id": thread_id}}
        context = ProcurementContext(user_id=user_id, username=username)
        state = initial_state(
            task_id=task_id,
            raw_request=raw_request,
            user_id=user_id,
            username=username,
            conversation_id=conversation_id,
            request=request,
            now=now.isoformat(),
        )

        try:
            await self._graph.ainvoke(state, config, context=context)
        except Exception as exc:
            await self._tasks.update_status(
                task_id=task_id,
                status=TaskStatus.FAILED.value,
                error={"code": "TASK_START_FAILED", "message": str(exc)},
                now=self._now(),
            )
            raise

        return await self._snapshot(task_id, config, context)

    async def resume(
        self,
        *,
        task_id: str,
        user_id: str,
        username: str,
        resume_data: dict,
    ) -> TaskRunResult:
        record = await self._tasks.get(task_id)
        if record is None or record.user_id != user_id:
            raise PermissionError("TASK_NOT_FOUND_OR_FORBIDDEN")

        config = {"configurable": {"thread_id": record.thread_id}}
        context = ProcurementContext(user_id=user_id, username=username)
        snapshot = await self._graph.aget_state(config)

        if not snapshot.values:
            await self._tasks.update_status(
                task_id=task_id,
                status=TaskStatus.ORPHANED.value,
                error={"code": "CHECKPOINT_MISSING", "message": "任务检查点不存在"},
                now=self._now(),
            )
            raise TaskCheckpointMissing(record.thread_id)

        if not snapshot.next:
            # 已经终态：幂等返回，不重复执行
            return await self._snapshot(task_id, config, context)

        input_value = (
            Command(resume=resume_data)
            if snapshot.interrupts
            else None
        )
        await self._tasks.mark_resumed(task_id, self._now())
        await self._graph.ainvoke(input_value, config, context=context)
        return await self._snapshot(task_id, config, context)

    async def get(self, *, task_id: str, user_id: str) -> TaskRunResult:
        record = await self._tasks.get(task_id)
        if record is None or record.user_id != user_id:
            raise PermissionError("TASK_NOT_FOUND_OR_FORBIDDEN")
        return await self._snapshot(
            task_id,
            {"configurable": {"thread_id": record.thread_id}},
            ProcurementContext(user_id=user_id, username=user_id),
        )

    async def reconcile(self, *, limit: int = 200) -> int:
        """启动时对账：只修正任务记录，不自动执行图。"""
        checked = 0
        for record in await self._tasks.list_recoverable(limit=limit):
            checked += 1
            config = {"configurable": {"thread_id": record.thread_id}}
            snapshot = await self._graph.aget_state(config)

            if not snapshot.values:
                await self._tasks.update_status(
                    task_id=record.task_id,
                    status=TaskStatus.ORPHANED.value,
                    error={
                        "code": "CHECKPOINT_MISSING",
                        "message": "业务任务存在，但 Checkpoint 不存在",
                    },
                    now=self._now(),
                )
                continue

            values = snapshot.values or {}
            next_node = snapshot.next[0] if snapshot.next else None
            await self._tasks.update_status(
                task_id=record.task_id,
                status=values.get("status", record.status),
                current_node=next_node,
                retry_from=values.get("retry_from"),
                error=values.get("last_error"),
                now=self._now(),
            )
        return checked

    async def _snapshot(self, task_id: str, config: dict, context) -> TaskRunResult:
        snapshot = await self._graph.aget_state(config)
        values = snapshot.values or {}
        current_node = snapshot.next[0] if snapshot.next else None

        await self._tasks.update_status(
            task_id=task_id,
            status=values.get("status", TaskStatus.RUNNING.value),
            current_node=current_node,
            retry_from=values.get("retry_from"),
            error=values.get("last_error"),
            now=self._now(),
        )

        interrupt_payload = None
        if snapshot.interrupts:
            interrupt_payload = snapshot.interrupts[0].value

        return TaskRunResult(
            task_id=task_id,
            thread_id=config["configurable"]["thread_id"],
            status=values.get("status", TaskStatus.RUNNING.value),
            current_node=current_node,
            interrupt=interrupt_payload,
            state_summary={
                "plan_version": values.get("plan_version"),
                "approval_status": values.get("approval_status"),
                "execution_status": values.get("execution_status"),
                "order_id": values.get("order_id"),
                "last_error": values.get("last_error"),
            },
            recoverable=bool(snapshot.next),
        )
