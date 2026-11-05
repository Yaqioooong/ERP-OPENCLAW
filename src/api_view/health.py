"""健康检查与就绪探针。

- ``/live`` 只表示进程活着，不检查外部依赖；
- ``/ready`` 逐项检查 Redis/PostgreSQL/ERP MCP/沙箱/Agent/采购 runtime；
- ``/health`` 保留兼容入口，健康时 200，不健康时 503，不再返回假 healthy。

检查结果带 5s 缓存和单项 2s 超时；错误文本会脱敏并截断，不返回 DSN/密码。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agent.config import REDIS_CLIENT, SANDBOX_CONFIG
from agent.procurement.dependencies import (
    reconciliation_worker_ready,
    task_runner_ready,
)
from agent.tools.mcp_client import MCP_SERVER_CONFIG
from api_view.agent_loader import agent_loader
from api_view.web_config import API_DESCRIPTION, API_TITLE, API_VERSION

router = APIRouter()

CHECK_TIMEOUT_SECONDS = float(
    os.getenv("HEALTH_CHECK_TIMEOUT_SECONDS", "2")
)
CACHE_TTL_SECONDS = float(os.getenv("HEALTH_CACHE_TTL_SECONDS", "5"))

_DSN_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^@\s/]+)@")
_cache: dict[str, Any] | None = None
_cache_at: float = 0.0
_cache_lock = asyncio.Lock()


def _safe_error(exc: BaseException, *, limit: int = 240) -> str:
    text = _DSN_RE.sub(r"\1***@", str(exc))
    return text[:limit]


def _error_check(
    exc: BaseException,
    *,
    latency_ms: int,
) -> dict[str, Any]:
    return {
        "ok": False,
        "latency_ms": latency_ms,
        "error_type": type(exc).__name__,
        "error": _safe_error(exc),
    }


async def check_redis() -> dict[str, Any]:
    started = time.monotonic()
    try:
        await asyncio.wait_for(
            asyncio.to_thread(REDIS_CLIENT.ping),
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return _error_check(
            exc, latency_ms=int((time.monotonic() - started) * 1000)
        )


async def check_postgres() -> dict[str, Any]:
    started = time.monotonic()
    try:
        from agent.procurement.dependencies import get_database

        database = await get_database()
        async with database.acquire() as conn:
            await asyncio.wait_for(
                conn.fetchval("SELECT 1"),
                timeout=CHECK_TIMEOUT_SECONDS,
            )
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return _error_check(
            exc, latency_ms=int((time.monotonic() - started) * 1000)
        )


def _tcp_target(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "tcp"}:
        raise ValueError(f"unsupported url scheme: {parsed.scheme}")
    host = parsed.hostname
    if not host:
        raise ValueError("url host missing")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, int(port)


async def _check_tcp(url: str, *, latency_started: float) -> dict[str, Any]:
    host, port = _tcp_target(url)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=CHECK_TIMEOUT_SECONDS,
    )
    try:
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - latency_started) * 1000),
            "target": f"{host}:{port}",
        }
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def check_erp_mcp() -> dict[str, Any]:
    started = time.monotonic()
    try:
        url = str(MCP_SERVER_CONFIG["erp-api"]["url"])
        return await _check_tcp(url, latency_started=started)
    except Exception as exc:
        return _error_check(
            exc, latency_ms=int((time.monotonic() - started) * 1000)
        )


async def check_sandbox() -> dict[str, Any]:
    started = time.monotonic()
    try:
        url = str(getattr(SANDBOX_CONFIG, "domain", ""))
        return await _check_tcp(url, latency_started=started)
    except Exception as exc:
        return _error_check(
            exc, latency_ms=int((time.monotonic() - started) * 1000)
        )


def check_agent() -> dict[str, Any]:
    ready = bool(agent_loader.is_ready)
    result: dict[str, Any] = {"ok": ready}
    if not ready and agent_loader.last_error:
        result["error_type"] = "AgentNotReady"
        result["error"] = agent_loader.last_error[:240]
    return result


def check_procurement() -> dict[str, Any]:
    runner = bool(task_runner_ready())
    worker = bool(reconciliation_worker_ready())
    return {
        "ok": runner and worker,
        "task_runner": runner,
        "reconciliation_worker": worker,
    }


_CHECKS: tuple[tuple[str, Callable[[], Any]], ...] = (
    ("redis", check_redis),
    ("postgres", check_postgres),
    ("erp_mcp", check_erp_mcp),
    ("sandbox", check_sandbox),
    ("agent", check_agent),
    ("procurement", check_procurement),
)


async def _run_check(
    name: str,
    check: Callable[[], Any],
) -> dict[str, Any]:
    try:
        result = check()
        if asyncio.iscoroutine(result):
            result = await asyncio.wait_for(
                result, timeout=CHECK_TIMEOUT_SECONDS + 0.5
            )
        if not isinstance(result, dict):
            return {"ok": bool(result)}
        return result
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error_type": "TimeoutError",
            "error": f"check timeout after {CHECK_TIMEOUT_SECONDS:g}s",
        }
    except Exception as exc:
        return _error_check(exc, latency_ms=0)


def _startup_errors(request: Request | None) -> dict[str, str]:
    if request is None:
        return {}
    errors = getattr(request.app.state, "startup_errors", None)
    if not isinstance(errors, dict):
        return {}
    return {
        str(key): str(value)[:240]
        for key, value in errors.items()
    }


def _is_initializing(request: Request | None) -> bool:
    if request is None:
        return False
    task = getattr(request.app.state, "init_task", None)
    return bool(task is not None and not task.done())


async def _build_report(request: Request | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name, check in _CHECKS:
        checks[name] = await _run_check(name, check)

    all_ok = all(item.get("ok") for item in checks.values())
    if not all_ok and _is_initializing(request):
        status = "initializing"
    elif all_ok:
        status = "ready"
    else:
        status = "degraded"

    return {
        "status": status,
        "service": API_TITLE,
        "version": API_VERSION,
        "description": API_DESCRIPTION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "checks": checks,
        "startup_errors": _startup_errors(request),
    }


async def build_health_report(
    request: Request | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """返回缓存的健康报告；缓存 TTL 到期或 force=True 时重新检查。"""
    global _cache, _cache_at
    now = time.monotonic()
    if not force and _cache is not None and now - _cache_at < CACHE_TTL_SECONDS:
        cached = dict(_cache)
        cached["startup_errors"] = _startup_errors(request)
        if _is_initializing(request) and cached.get("status") != "ready":
            cached["status"] = "initializing"
        return cached

    async with _cache_lock:
        now = time.monotonic()
        if (
            not force
            and _cache is not None
            and now - _cache_at < CACHE_TTL_SECONDS
        ):
            cached = dict(_cache)
            cached["startup_errors"] = _startup_errors(request)
            if _is_initializing(request) and cached.get("status") != "ready":
                cached["status"] = "initializing"
            return cached

        report = await _build_report(request)
        _cache = report
        _cache_at = time.monotonic()
        return report


def reset_health_cache() -> None:
    """测试/重载用：清空健康检查缓存。"""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


@router.get("/live", tags=["系统"])
async def live():
    """进程存活探针：不检查外部依赖。"""
    return {
        "status": "alive",
        "service": API_TITLE,
        "version": API_VERSION,
    }


@router.get("/ready", tags=["系统"])
async def ready(request: Request):
    report = await build_health_report(request)
    status_code = 200 if report["status"] == "ready" else 503
    return JSONResponse(report, status_code=status_code)


@router.get("/health", tags=["系统"])
async def health(request: Request):
    """兼容旧启动脚本：健康 200 + healthy，不健康 503 + degraded。"""
    report = await build_health_report(request)
    healthy = report["status"] == "ready"
    body = {
        **report,
        "status": "healthy" if healthy else "degraded",
        "phase": report["status"],
    }
    return JSONResponse(body, status_code=200 if healthy else 503)


__all__ = [
    "router",
    "build_health_report",
    "reset_health_cache",
    "check_redis",
    "check_postgres",
    "check_erp_mcp",
    "check_sandbox",
    "check_agent",
    "check_procurement",
]
