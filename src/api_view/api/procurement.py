from fastapi import APIRouter, Header, HTTPException
from fastapi.params import Depends
from pydantic import BaseModel

from agent.procurement.dependencies import get_task_runner
from agent.procurement.task_runner import TaskCheckpointMissing

router = APIRouter()


class StartTaskRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    request: dict | None = None


class ResumeTaskRequest(BaseModel):
    resume: dict


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
