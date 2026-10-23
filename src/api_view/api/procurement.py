from fastapi import APIRouter, Header, HTTPException
from fastapi.params import Depends
from pydantic import BaseModel
from typing import Literal

from agent.procurement.dependencies import get_task_runner, get_order_reconciler
from agent.procurement.task_runner import TaskCheckpointMissing

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


def get_current_user(
        x_user_id: str | None = Header(default=None, alias="X-User-Id"),
        x_username: str | None = Header(default=None, alias="X-Username")
) -> tuple[str, str]:
    # 开发占位：生产替换为 JWT/Session 认证
    user_id = x_user_id or "guoyaxing"
    return user_id, x_username or user_id


@router.post("/procurement/tasks")
async def start_task(request: StartTaskRequest, user=Depends(get_current_user)):
    try:
        runner = await get_task_runner()
        return await runner.start(
            raw_request=request.message,
            user_id=user[0],
            username=user[1],
            conversation_id=request.conversation_id,
            request=request.request,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/procurement/tasks/{task_id}/resume")
async def resume_task(
        task_id: str,
        request: ResumeTaskRequest,
        user=Depends(get_current_user),
):
    try:
        runner = await get_task_runner()
        return await runner.resume(
            task_id=task_id,
            user_id=user[0],
            username=user[1],
            resume_data=request.resume,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TaskCheckpointMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/procurement/tasks/{task_id}")
async def get_task(task_id: str, user=Depends(get_current_user)):
    try:
        runner = await get_task_runner()
        return await runner.get(task_id=task_id, user_id=user[0])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/procurement/executions/unknown")
async def list_unknown_executions(
        limit: int = 50,
        user=Depends(get_current_user),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        records = await reconciler.list_pending(limit=limit)
        items = []
        for record in records:
            task = await runner.get_record(record.task_id)
            if task is None or task.user_id != user[0]:
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
        user=Depends(get_current_user),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        record = await reconciler.get_execution(idempotency_key)
        if record is None:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        task = await runner.get_record(record.task_id)
        if task is None or task.user_id != user[0]:
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
        user=Depends(get_current_user),
):
    try:
        reconciler = await get_order_reconciler()
        runner = await get_task_runner()
        record = await reconciler.get_execution(idempotency_key)
        if record is None:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        task = await runner.get_record(record.task_id)
        if task is None or task.user_id != user[0]:
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
