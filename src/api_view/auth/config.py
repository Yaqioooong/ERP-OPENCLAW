"""认证配置（环境变量集中读取）。"""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# 认证用户库；默认与采购库同库不同表。
AUTH_DATABASE_URL = os.getenv(
    "AUTH_DATABASE_URL",
    os.getenv("PROCUREMENT_DATABASE_URL", "postgresql:///erp_openclaw"),
)

AUTH_REDIS_URL = os.getenv(
    "AUTH_REDIS_URL",
    os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
)

SESSION_TTL_SECONDS = int(os.getenv("AUTH_SESSION_TTL_SECONDS", "28800"))
COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "erp_session")
COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", IS_PRODUCTION)
COOKIE_SAMESITE = "lax"

LOGIN_MAX_FAILURES = int(os.getenv("AUTH_LOGIN_MAX_FAILURES", "10"))
LOGIN_WINDOW_SECONDS = int(os.getenv("AUTH_LOGIN_WINDOW_SECONDS", "300"))

REGISTER_MAX_ATTEMPTS = int(os.getenv("AUTH_REGISTER_MAX_ATTEMPTS", "5"))
REGISTER_WINDOW_SECONDS = int(os.getenv("AUTH_REGISTER_WINDOW_SECONDS", "3600"))

_origins = os.getenv(
    "AUTH_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
ALLOWED_ORIGINS = tuple(origin.strip() for origin in _origins.split(",") if origin.strip())

# 仅开发模式可用的显式演示身份；生产环境无论是否配置都不会启用。
DEV_DEMO_USER_ID = os.getenv("AUTH_DEV_DEMO_USER_ID", "").strip()
DEV_DEMO_USERNAME = os.getenv("AUTH_DEV_DEMO_USERNAME", "").strip()
DEV_DEMO_ROLES = tuple(
    role.strip()
    for role in os.getenv("AUTH_DEV_DEMO_ROLES", "requester").split(",")
    if role.strip()
)


def dev_demo_enabled() -> bool:
    return (not IS_PRODUCTION) and bool(DEV_DEMO_USER_ID)


def origin_allowed(origin: str | None) -> bool:
    """没有 Origin（CLI/测试）时放行；有 Origin 时必须命中白名单。"""
    if not origin:
        return True
    return origin in ALLOWED_ORIGINS
