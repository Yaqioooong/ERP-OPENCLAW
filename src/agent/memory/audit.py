"""记忆读写审计日志。

只允许白名单字段进入日志；文件内容、供应商名、查询正文一律不得记录。
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

_ALLOWED_FIELDS = (
    "op",
    "user_id",
    "namespace",
    "path",
    "request_id",
    "thread_id",
    "outcome",
    "reason",
    "size",
    "revision",
    "message_ids",
)


def _clean(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value)[:300]


def log_memory_event(**fields: Any) -> None:
    """记录一条结构化记忆事件。

    调用方只能传白名单字段；未识别字段会被丢弃，避免误把正文写进日志。
    """
    payload = {
        key: _clean(fields.get(key))
        for key in _ALLOWED_FIELDS
        if fields.get(key) not in (None, "")
    }
    payload.setdefault("outcome", "ok")
    logger.info("memory_event {}", json.dumps(payload, ensure_ascii=False, sort_keys=True))
