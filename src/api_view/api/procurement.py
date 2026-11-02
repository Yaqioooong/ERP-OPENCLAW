from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal

from agent.procurement.dependencies import (
    get_order_reconciler,
    get_task_event_service,
    get_task_runner,
)
from agent.procurement.request_normalization import ProcurementRequestInputError
from agent.procurement.task_events import encode_sse
from agent.procurement.task_runner import TaskCheckpointMissing
from api_view.auth.models import AuthenticatedUser
from api_view.auth.permissions import require_password_ready

router = APIRouter()


class StartTaskRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    request: dict | None = None


class ResumeTaskRequest(BaseModel):
    resume: dict


class ResolveExecutionRequest(BaseModel):
    action: Literal["mark_succeeded", "mark_failed"]
    order_id: str | None = None
    comment: str = ""


@router.post("/procurement/tasks")
async def start_task(
        request: StartTaskRequest,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        runner = await get_task_runner()
        return await runner.start(
            raw_request=request.message,
            user_id=user.user_id,
            username=user.username,
            conversation_id=request.conversation_id,
            request=request.request,
            roles=user.roles,
        )
    except ProcurementRequestInputError as exc:
        raise HTTPException(status_code=422, detail=exc.as_detail()) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/procurement/tasks/{task_id}/resume")
async def resume_task(
        task_id: str,
        request: ResumeTaskRequest,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        runner = await get_task_runner()
        return await runner.resume(
            task_id=task_id,
            user_id=user.user_id,
            username=user.username,
            resume_data=request.resume,
            roles=user.roles,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TaskCheckpointMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/procurement/tasks/{task_id}/events")
async def stream_task_events(
        task_id: str,
        after: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias="Last-Event-ID"
        ),
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        runner = await get_task_runner()
        record = await runner.get_record(task_id)
        roles = set(user.roles)
        if record is None or (
                record.user_id != user.user_id
                and not (roles & {"approver", "admin"})
        ):
            raise HTTPException(status_code=403, detail="无权读取该任务事件")

        cursor = after
        if last_event_id:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError:
                pass

        service = await get_task_event_service()

        async def generator():
            async for event in service.stream(
                    task_id=task_id,
                    after_sequence=cursor,
            ):
                yield encode_sse(event)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/procurement/tasks/{task_id}")
async def get_task(
        task_id: str,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        runner = await get_task_runner()
        return await runner.get(
            task_id=task_id, user_id=user.user_id, roles=user.roles
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/procurement/executions/unknown")
async def list_unknown_executions(
        limit: int = 50,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        records = await reconciler.list_pending(limit=limit)
        items = []
        for record in records:
            task = await runner.get_record(record.task_id)
            if task is None or (
                task.user_id != user.user_id and "admin" not in user.roles
            ):
                continue
            items.append(
                {
                    "idempotency_key": record.idempotency_key,
                    "task_id": record.task_id,
                    "status": record.status.value,
                    "order_id": record.order_id,
                    "order_number": record.order_number,
                    "verification_attempts": record.verification_attempts,
                    "last_error_code": record.last_error_code,
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        return items
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/procurement/executions/{idempotency_key}/reconcile")
async def reconcile_execution(
        idempotency_key: str,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        record = await reconciler.get_execution(idempotency_key)
        if record is None:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        task = await runner.get_record(record.task_id)
        if task is None or (
            task.user_id != user.user_id and "admin" not in user.roles
        ):
            raise HTTPException(status_code=403, detail="无权操作该订单执行记录")
        return await reconciler.verify_by_key(idempotency_key)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/procurement/executions/{idempotency_key}/resolve")
async def resolve_execution(
        idempotency_key: str,
        request: ResolveExecutionRequest,
        user: AuthenticatedUser = Depends(require_password_ready),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        record = await reconciler.get_execution(idempotency_key)
        if record is None:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        task = await runner.get_record(record.task_id)
        if task is None or (
            task.user_id != user.user_id and "admin" not in user.roles
        ):
            raise HTTPException(status_code=403, detail="无权操作该订单执行记录")
        return await reconciler.manual_resolve(
            idempotency_key=idempotency_key,
            action=request.action,
            comment=request.comment,
            order_id=request.order_id,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
