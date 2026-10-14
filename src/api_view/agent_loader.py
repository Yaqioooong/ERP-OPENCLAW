"""Agent 加载器及 Redis 会话存储。"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from redis import Redis

from agent.config import (
    REDIS_CLIENT,
    REDIS_DISPLAY_PREFIX,
    REDIS_SESSION_META_PREFIX,
    REDIS_THREAD_SET_KEY,
    initialize_redis_checkpointer,
)
from agent import config as agent_config


class AgentLoader:
    """管理 Agent 实例，以及 Redis 中的会话展示数据。"""

    _instance: Optional["AgentLoader"] = None
    _redis_client: Optional[Redis] = None
    _initialized = False
    _agent = None
    _MAX_FIELD_LENGTH = 500_000

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self):
        """检查 Redis 连接并懒加载 Agent。"""
        if self._initialized and self._agent is not None:
            return self._agent

        print("[AgentLoader] 开始初始化...")
        try:
            self._redis_client = REDIS_CLIENT
            self._redis_client.ping()
            # astream/ainvoke requires an async checkpointer implementation.
            # Initialize it before importing main_agent, which captures the
            # module-level CHECKPOINTER used when compiling the graph.
            await initialize_redis_checkpointer()
            from agent.main_agent import get_agent_async

            self._agent = await get_agent_async()
            self._initialized = True
            print("[AgentLoader] 初始化完成")
        except Exception:
            self._redis_client = None
            raise
        return self._agent

    @property
    def agent(self):
        if self._agent is None:
            raise RuntimeError("Agent 未初始化，请先调用 initialize() 方法")
        return self._agent

    def create_config(
        self,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id or str(uuid.uuid4()),
                "user_id": user_id or "guoyaxing",
                **kwargs,
            }
        }

    async def get_state_history(self, thread_id: str, limit: int = 50) -> List[Any]:
        states = []
        async for state in self.agent.aget_state_history(self.create_config(thread_id)):
            states.append(state)
            if len(states) >= limit:
                break
        return states

    async def get_current_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        try:
            state = await self.agent.aget_state(self.create_config(thread_id))
            if state and state.values:
                return state.values.get("messages", [])
        except Exception as exc:
            print(f"[AgentLoader] 获取消息失败: {exc}")
        return []

    def get_all_thread_ids(self) -> List[str]:
        if self._redis_client is None:
            return []
        values = self._redis_client.smembers(REDIS_THREAD_SET_KEY)
        return sorted(
            value.decode("utf-8") if isinstance(value, bytes) else str(value)
            for value in values
            if value and value not in ("*", b"*")
        )

    def get_session_updated_at(self, thread_id: str) -> datetime:
        if self._redis_client is None:
            return datetime.now()
        try:
            value = self._redis_client.hget(
                f"{REDIS_SESSION_META_PREFIX}{thread_id}", "updated_at"
            )
            if value:
                text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                return datetime.fromisoformat(text)
        except Exception as exc:
            print(f"[AgentLoader] 获取会话时间失败: {exc}")
        return datetime.now()

    async def delete_session(self, thread_id: str) -> bool:
        if self._redis_client is None:
            return False
        try:
            checkpointer = agent_config.CHECKPOINTER
            if checkpointer is not None:
                await checkpointer.adelete_thread(thread_id)
            self._redis_client.delete(
                f"{REDIS_DISPLAY_PREFIX}{thread_id}",
                f"{REDIS_SESSION_META_PREFIX}{thread_id}",
            )
            self._redis_client.srem(REDIS_THREAD_SET_KEY, thread_id)
            print(f"[AgentLoader] 已删除会话 {thread_id}")
            return True
        except Exception as exc:
            print(f"[AgentLoader] 删除会话失败: {exc}")
            return False

    @classmethod
    def _truncate_message_fields(cls, msg: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(msg)
        for field in ("text", "content", "args"):
            value = result.get(field)
            if isinstance(value, str) and len(value) > cls._MAX_FIELD_LENGTH:
                result[field] = value[: cls._MAX_FIELD_LENGTH] + "\n\n...(内容过长已截断)"
        return result

    async def save_display_messages(
        self, thread_id: str, messages: List[Dict[str, Any]]
    ) -> bool:
        if self._redis_client is None:
            return False
        try:
            normalized = [
                self._truncate_message_fields(message)
                if isinstance(message, dict)
                else {"content": str(message)}
                for message in messages
            ]
            now = datetime.now().astimezone()
            self._redis_client.set(
                f"{REDIS_DISPLAY_PREFIX}{thread_id}",
                json.dumps(normalized, ensure_ascii=False, default=str),
            )
            self._redis_client.hset(
                f"{REDIS_SESSION_META_PREFIX}{thread_id}",
                mapping={"updated_at": now.isoformat()},
            )
            self._redis_client.sadd(REDIS_THREAD_SET_KEY, thread_id)
            print(f"[AgentLoader] 已保存 {len(normalized)} 条展示消息，thread_id={thread_id}")
            return True
        except Exception as exc:
            print(f"[AgentLoader] 保存展示消息失败: {exc}")
            return False

    async def get_display_messages(
        self, thread_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        if self._redis_client is None:
            return None
        try:
            raw = self._redis_client.get(f"{REDIS_DISPLAY_PREFIX}{thread_id}")
            if not raw:
                return None
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            return json.loads(text)
        except Exception as exc:
            print(f"[AgentLoader] 读取展示消息失败: {exc}")
            return None

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        checkpointer = agent_config.CHECKPOINTER
        if checkpointer is not None:
            redis_async_client = getattr(checkpointer, "_redis", None)
            if redis_async_client is not None:
                await redis_async_client.aclose()
            agent_config.CHECKPOINTER = None
        if self._redis_client is not None:
            self._redis_client.close()
            self._redis_client = None
        self._initialized = False


agent_loader = AgentLoader()
