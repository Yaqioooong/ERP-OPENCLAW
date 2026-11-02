"""自动记忆中间件：候选生成与持久化分离。

本中间件只做编排：
  messages → MemoryCandidateGenerator → PreferenceMemoryService.apply_candidates

候选来源/白名单由生成器与服务层负责；这里不调用 LLM 提取供应商，也不直接
读写偏好文件。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage

from agent.log_utils import MyLogger
from agent.memory.audit import log_memory_event
from agent.memory.candidate_generator import MemoryCandidateGenerator
from agent.memory.identity import (
    MemoryIdentityError,
    preferences_key,
    require_user_id,
)
from agent.memory.preference_service import PreferenceMemoryService

logger = MyLogger().get_logger()


class MemoryUpdateMiddleware(AgentMiddleware):
    """生成候选并提交持久化；失败不阻断对话。"""

    def __init__(
        self,
        *,
        generator: MemoryCandidateGenerator,
        preference_service: PreferenceMemoryService,
    ) -> None:
        super().__init__()
        self.generator = generator
        self.preference_service = preference_service

    def after_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        return None

    async def aafter_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        ctx = getattr(runtime, "context", None)
        request_id = str(getattr(ctx, "request_id", "") or "")
        thread_id = str(getattr(ctx, "thread_id", "") or "")

        try:
            user_id = require_user_id(ctx)
        except MemoryIdentityError:
            logger.error(
                "MemoryUpdateMiddleware: missing trusted user_id, "
                "refuse memory write request_id={} thread_id={}",
                request_id,
                thread_id,
            )
            return None

        try:
            messages: List[BaseMessage] = state.get("messages", [])
            if not messages:
                return None

            candidates = await self.generator.generate(
                messages,
                thread_id=thread_id,
                request_id=request_id,
            )
            if not candidates:
                return None

            result = await self.preference_service.apply_candidates(
                user_id,
                candidates,
                request_id=request_id,
                thread_id=thread_id,
            )
            type_counts = dict(Counter(c.memory_type for c in candidates))
            origin_counts = dict(Counter(c.origin for c in candidates))

            if not result.ok:
                logger.warning(
                    "MemoryUpdateMiddleware: commit failed user={} reason={} "
                    "retryable={} applied={} rejected={} request_id={} thread_id={}",
                    user_id,
                    result.reason,
                    result.retryable,
                    result.applied_count,
                    result.rejected_count,
                    request_id,
                    thread_id,
                )
                log_memory_event(
                    op="memory.update_preferences",
                    user_id=user_id,
                    namespace=(user_id,),
                    path=preferences_key(user_id),
                    request_id=request_id,
                    thread_id=thread_id,
                    outcome="error",
                    reason=result.reason,
                )
                return None

            if result.changed:
                logger.info(
                    "MemoryUpdateMiddleware: updated user={} revision={} "
                    "types={} origins={} applied={} rejected={} "
                    "request_id={} thread_id={}",
                    user_id,
                    result.revision,
                    type_counts,
                    origin_counts,
                    result.applied_count,
                    result.rejected_count,
                    request_id,
                    thread_id,
                )
            else:
                logger.debug(
                    "MemoryUpdateMiddleware: noop user={} types={} origins={} "
                    "rejected={} request_id={}",
                    user_id,
                    type_counts,
                    origin_counts,
                    result.rejected_count,
                    request_id,
                )
        except Exception as exc:
            logger.warning(
                "MemoryUpdateMiddleware: update failed user={} request_id={} "
                "error_type={}",
                locals().get("user_id", ""),
                request_id,
                type(exc).__name__,
            )

        return None
