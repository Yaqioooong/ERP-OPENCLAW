"""聊天会话归属：thread_id 与创建者的绑定。

旧会话没有归属信息，默认拒绝访问；不自动认领，避免攻击者抢占历史会话。
"""
from __future__ import annotations

from typing import Any

import redis

from api_view.auth.sessions import get_redis_client

THREAD_OWNER_PREFIX = "erp:chat:thread_owner:"


class ThreadAccessError(RuntimeError):
    """当前用户不能访问该会话。"""


class ThreadOwnershipUnavailable(RuntimeError):
    """归属存储不可用，必须失败关闭。"""


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class ThreadOwnershipStore:
    def __init__(self, client: redis.Redis):
        self._client = client

    @staticmethod
    def _key(thread_id: str) -> str:
        return f"{THREAD_OWNER_PREFIX}{thread_id}"

    def claim(self, thread_id: str, user_id: str) -> None:
        """新建会话时绑定；若已被绑定则校验，不覆盖。"""
        if not thread_id or not user_id:
            raise ThreadAccessError("会话或用户标识为空")
        try:
            created = self._client.set(self._key(thread_id), user_id, nx=True)
        except Exception as exc:
            raise ThreadOwnershipUnavailable("会话归属存储不可用") from exc
        if created:
            return
        self.verify(thread_id, user_id)

    def verify(self, thread_id: str, user_id: str) -> None:
        if not thread_id or not user_id:
            raise ThreadAccessError("会话或用户标识为空")
        try:
            owner = self._client.get(self._key(thread_id))
        except Exception as exc:
            raise ThreadOwnershipUnavailable("会话归属存储不可用") from exc
        if owner is None:
            raise ThreadAccessError("会话不存在或未绑定用户")
        if _as_text(owner) != user_id:
            raise ThreadAccessError("无权访问该会话")

    def get_owner(self, thread_id: str) -> str | None:
        try:
            owner = self._client.get(self._key(thread_id))
        except Exception as exc:
            raise ThreadOwnershipUnavailable("会话归属存储不可用") from exc
        return _as_text(owner) if owner is not None else None


_store: ThreadOwnershipStore | None = None


def get_thread_ownership_store() -> ThreadOwnershipStore:
    global _store
    if _store is None:
        _store = ThreadOwnershipStore(get_redis_client())
    return _store
