"""认证审计日志：只记录事件与主体，不记录密码/哈希/token。"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

_ALLOWED = ("event", "actor", "target", "client_ip", "outcome", "detail")


def log_auth_event(**fields: Any) -> None:
    payload = {
        key: str(fields.get(key, ""))[:200]
        for key in _ALLOWED
        if fields.get(key) not in (None, "")
    }
    payload.setdefault("outcome", "ok")
    logger.info("auth_event {}", json.dumps(payload, ensure_ascii=False, sort_keys=True))
