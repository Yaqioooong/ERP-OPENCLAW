"""健康检查与启动降级测试（规格 specs/013）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from api_view import health


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(health.router)
    app.state.startup_errors = {}
    return app


def _checks(ok: bool = True, *, delay: float = 0.0, error: str | None = None):
    async def check():
        if delay:
            import asyncio

            await asyncio.sleep(delay)
        if error:
            raise RuntimeError(error)
        return {"ok": ok}

    return tuple(
        (name, check)
        for name in ("redis", "postgres", "erp_mcp", "sandbox", "agent", "procurement")
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    health.reset_health_cache()
    yield
    health.reset_health_cache()


@pytest.mark.anyio
async def test_live_is_independent_of_dependencies(monkeypatch):
    monkeypatch.setattr(health, "_CHECKS", _checks(ok=False))
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


@pytest.mark.anyio
async def test_ready_and_health_when_all_checks_pass(monkeypatch):
    monkeypatch.setattr(health, "_CHECKS", _checks(ok=True))
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        ready = await client.get("/ready")
        healthy = await client.get("/health")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert all(item["ok"] for item in ready.json()["checks"].values())

    assert healthy.status_code == 200
    assert healthy.json()["status"] == "healthy"
    assert healthy.json()["phase"] == "ready"


@pytest.mark.anyio
async def test_degraded_reports_failed_check_without_hiding_others(monkeypatch):
    checks = list(_checks(ok=True))
    checks[0] = ("redis", _checks(ok=False)[0][1])
    monkeypatch.setattr(health, "_CHECKS", tuple(checks))
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
        legacy = await client.get("/health")

    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["ok"] is False
    assert body["checks"]["postgres"]["ok"] is True

    assert legacy.status_code == 503
    assert legacy.json()["status"] == "degraded"
    assert legacy.json()["phase"] == "degraded"


@pytest.mark.anyio
async def test_initializing_status_when_init_task_pending(monkeypatch):
    checks = list(_checks(ok=True))
    checks[0] = ("redis", _checks(ok=False)[0][1])
    monkeypatch.setattr(health, "_CHECKS", tuple(checks))
    app = _app()
    app.state.init_task = __import__("asyncio").Future()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "initializing"


@pytest.mark.anyio
async def test_health_cache_reuses_result(monkeypatch):
    calls = {"count": 0}

    async def check():
        calls["count"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        health,
        "_CHECKS",
        (("redis", check),),
    )
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/ready")
        second = await client.get("/ready")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1


@pytest.mark.anyio
async def test_check_timeout_returns_failure_not_hang(monkeypatch):
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        health,
        "_CHECKS",
        (("redis", _checks(ok=True, delay=1.0)[0][1]),),
    )
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["error_type"] == "TimeoutError"


@pytest.mark.anyio
async def test_health_errors_are_masked(monkeypatch):
    monkeypatch.setattr(
        health,
        "_CHECKS",
        (
            (
                "postgres",
                _checks(
                    error="postgresql://user:secret@db.example/erp failed"
                )[0][1],
            ),
        ),
    )
    app = _app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    body = response.text
    assert response.status_code == 503
    assert "secret" not in body
    assert "***@" in body


def test_chat_requires_agent_ready(monkeypatch):
    from api_view.api import chat

    monkeypatch.setattr(chat.agent_loader, "_initialized", False)
    monkeypatch.setattr(chat.agent_loader, "_agent", None)

    with pytest.raises(HTTPException) as caught:
        chat._require_agent_ready()

    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "AGENT_NOT_READY"


def test_lazy_store_and_background_startup_sources():
    root = Path(__file__).resolve().parents[1]
    config = (root / "src/agent/config.py").read_text(encoding="utf-8")
    loader = (root / "src/api_view/agent_loader.py").read_text(encoding="utf-8")
    web_main = (root / "src/api_view/web_main.py").read_text(encoding="utf-8")
    start_web = (root / "start_web.py").read_text(encoding="utf-8")

    assert "STORE.setup()" not in config
    assert "initialize_redis_store" in config
    assert "initialize_redis_store" in loader

    assert "asyncio.create_task" in web_main
    assert "startup_errors" in web_main
    assert "api_view.health" in web_main

    assert "/ready" in start_web


def test_agent_loader_readiness_and_last_error(monkeypatch):
    from api_view.agent_loader import agent_loader

    monkeypatch.setattr(agent_loader, "_initialized", False)
    monkeypatch.setattr(agent_loader, "_agent", None)
    monkeypatch.setattr(agent_loader, "_last_error", "RuntimeError: redis down")

    assert agent_loader.is_ready is False
    assert agent_loader.last_error == "RuntimeError: redis down"
    assert health.check_agent()["ok"] is False


@pytest.mark.anyio
async def test_startup_errors_are_exposed(monkeypatch):
    monkeypatch.setattr(health, "_CHECKS", _checks(ok=True))
    app = _app()
    app.state.startup_errors = {"agent": "RuntimeError: mcp down"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["startup_errors"] == {"agent": "RuntimeError: mcp down"}
