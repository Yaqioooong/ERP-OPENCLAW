"""采购状态图的统一诊断日志。

只在图边界记录白名单摘要，避免节点各自记录完整状态或上游原文。
"""
from __future__ import annotations

import functools
import time
from collections.abc import Callable, Mapping
from typing import Any

from langgraph.errors import GraphInterrupt

from agent.log_utils import MyLogger

logger = MyLogger().get_logger()

_MAX_ERROR_MESSAGE_LENGTH = 500
_SUMMARY_FIELDS = (
    "status",
    "plan_version",
    "approval_status",
    "execution_status",
    "retry_from",
    "order_id",
)


def _single_line(value: Any, *, limit: int = _MAX_ERROR_MESSAGE_LENGTH) -> str:
    if value is None or value == "":
        return "-"
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…"


def _summary(state: Mapping[str, Any] | None) -> str:
    state = state or {}
    return " ".join(
        f"{field}={_single_line(state.get(field), limit=120)}"
        for field in _SUMMARY_FIELDS
    )


def _result_state(
        state: Mapping[str, Any] | None,
        result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(state or {})
    if isinstance(result, Mapping):
        merged.update(result)
    return merged


def _result_error(
        state: Mapping[str, Any],
        result: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(result, Mapping) and "last_error" in result:
        error = result.get("last_error")
        return error if isinstance(error, Mapping) else None
    if state.get("status") == "failed":
        error = state.get("last_error")
        return error if isinstance(error, Mapping) else None
    return None


def trace_node(name: str, fn: Callable) -> Callable:
    """包装单个图节点；保留原返回值和中断/异常传播语义。"""

    @functools.wraps(fn)
    async def wrapped(state, runtime):
        started = time.perf_counter()
        logger.info(
            f"procurement.node.start task_id="
            f"{_single_line((state or {}).get('task_id'))} node={name} "
            f"{_summary(state)}"
        )
        try:
            result = await fn(state, runtime)
        except GraphInterrupt as exc:
            logger.info(
                f"procurement.node.interrupted task_id="
                f"{_single_line((state or {}).get('task_id'))} node={name} "
                f"{_summary(state)} duration_ms="
                f"{int((time.perf_counter() - started) * 1000)} "
                f"interrupt_type={type(exc).__name__}"
            )
            raise
        except Exception as exc:
            logger.error(
                f"procurement.node.error task_id="
                f"{_single_line((state or {}).get('task_id'))} node={name} "
                f"{_summary(state)} duration_ms="
                f"{int((time.perf_counter() - started) * 1000)} "
                f"error_code={type(exc).__name__} error_message="
                f"{_single_line(exc)}"
            )
            raise

        state_after = _result_state(state, result)
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            f"procurement.node.result task_id="
            f"{_single_line(state_after.get('task_id'))} node={name} "
            f"{_summary(state_after)} duration_ms={duration_ms}"
        )

        error = _result_error(state_after, result)
        if error:
            logger.error(
                f"procurement.node.error task_id="
                f"{_single_line(state_after.get('task_id'))} node={name} "
                f"{_summary(state_after)} duration_ms={duration_ms} "
                f"error_code={_single_line(error.get('code'))} "
                f"error_message={_single_line(error.get('message'))}"
            )
        return result

    return wrapped


def trace_route(
        source: str,
        fn: Callable,
        destinations: Mapping[str, str],
) -> Callable:
    """包装条件路由，记录实际选中的路由键和目标节点。"""

    @functools.wraps(fn)
    def wrapped(state):
        route = fn(state)
        logger.info(
            f"procurement.node.route task_id="
            f"{_single_line((state or {}).get('task_id'))} from={source} "
            f"route={_single_line(route)} "
            f"to={_single_line(destinations.get(route))} {_summary(state)}"
        )
        return route

    return wrapped
