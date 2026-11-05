"""
DeepAgent Chat API - FastAPI 主应用

提供基于 DeepAgent 的 AI 对话系统后端 API。
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_view.api import chat, history, memory, procurement
from api_view.agent_loader import agent_loader
from api_view.auth import config as auth_config
from api_view.auth.admin_router import router as admin_router
from api_view.auth.router import router as auth_router
from api_view.auth.users import get_user_repository
from api_view.health import router as health_router
from api_view.web_config import API_DESCRIPTION, API_TITLE, API_VERSION

_DSN_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^@\s/]+)@")


def _safe_error(exc: BaseException) -> str:
    text = _DSN_RE.sub(r"\1***@", str(exc))
    return f"{type(exc).__name__}: {text[:300]}"


async def _initialize_runtime(app: FastAPI) -> None:
    """后台初始化运行时。

    任何一步失败都只记录到 ``app.state.startup_errors``，不阻止应用启动：
    这样 ``/live`` 可用、``/ready`` 能报告具体失败项，而不是进程直接退出。
    """
    errors: dict[str, str] = {}

    try:
        auth_users = await get_user_repository()
        if await auth_users.count() == 0:
            print(
                "[auth] 用户表为空：请先执行 scripts/manage_auth_users.py create 开通账号；"
                "生产环境没有默认用户。"
            )
    except Exception as exc:
        errors["auth_database"] = _safe_error(exc)
        print(f"[startup] 认证用户库初始化失败: {errors['auth_database']}")

    try:
        await agent_loader.initialize()
    except Exception as exc:
        errors["agent"] = _safe_error(exc)
        print(f"[startup] Agent 初始化失败: {errors['agent']}")

    try:
        from agent.procurement.dependencies import get_task_runner

        await get_task_runner()
    except Exception as exc:
        errors["procurement_runtime"] = _safe_error(exc)
        print(f"[startup] 采购 runtime 初始化失败: {errors['procurement_runtime']}")

    try:
        from agent.procurement.dependencies import get_reconciliation_worker

        worker = await get_reconciliation_worker()
        worker.start()
    except Exception as exc:
        errors["reconciliation_worker"] = _safe_error(exc)
        print(f"[startup] 对账 worker 启动失败: {errors['reconciliation_worker']}")

    app.state.startup_errors = errors
    app.state.runtime_initialized = True
    if errors:
        print(f"[startup] 运行时以降级状态启动，失败项: {sorted(errors)}")
    else:
        print("[startup] 运行时初始化完成")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。

    启动时先创建后台初始化任务并立即返回，让 ``/live`` 和 ``/ready``
    在 Agent 30–60 秒初始化期间也能响应；关闭时等待/取消初始化任务再清理。
    """
    print("=" * 50)
    print("正在启动 DeepAgent Chat API...")
    print("=" * 50)

    app.state.startup_errors = {}
    app.state.runtime_initialized = False
    init_task = asyncio.create_task(
        _initialize_runtime(app),
        name="runtime-init",
    )
    app.state.init_task = init_task

    try:
        yield
    finally:
        print("=" * 50)
        print("正在关闭 DeepAgent Chat API...")
        print("=" * 50)

        if init_task.done():
            try:
                init_task.result()
            except Exception as exc:
                print(f"[shutdown] 初始化任务异常: {_safe_error(exc)}")
        else:
            try:
                await asyncio.wait_for(init_task, timeout=10)
            except Exception:
                init_task.cancel()
                await asyncio.gather(init_task, return_exceptions=True)

        from agent.backends.sandbox_setup import shutdown_sandboxes
        from agent.procurement.dependencies import (
            close_task_runner,
            stop_reconciliation_worker,
        )

        for cleanup in (
            stop_reconciliation_worker,
            close_task_runner,
            shutdown_sandboxes,
            agent_loader.close,
        ):
            try:
                await cleanup()
            except Exception as exc:
                print(f"[shutdown] {cleanup.__name__} 失败: {_safe_error(exc)}")


# 创建 FastAPI 应用
app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# ============================================================
# CORS 中间件配置
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(auth_config.ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 注册路由
# ============================================================
# 系统探针（/live、/ready、/health）
app.include_router(health_router, tags=["系统"])
# 认证接口
app.include_router(auth_router, prefix="/api", tags=["认证"])
app.include_router(admin_router, prefix="/api", tags=["用户管理"])
# 对话相关接口
app.include_router(chat.router, prefix="/api", tags=["对话"])
# 历史记录相关接口
app.include_router(history.router, prefix="/api", tags=["历史记录"])
# 长期记忆管理接口
app.include_router(memory.router, prefix="/api", tags=["长期记忆"])
# 采购任务相关接口
app.include_router(procurement.router, prefix="/api", tags=["采购任务"])


# ============================================================
# 根路径
# ============================================================
@app.get("/", tags=["首页"])
async def root():
    """返回 API 基本信息。"""
    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "description": "基于 DeepAgent 的 AI 对话系统 API",
        "docs": "/docs",
        "redoc": "/redoc",
    }
