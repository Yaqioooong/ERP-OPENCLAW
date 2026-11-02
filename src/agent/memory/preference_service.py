"""偏好提交服务：锁、revision、冲突重读重合并与结构化结果。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from pydantic import ValidationError

from agent.memory.audit import log_memory_event
from agent.memory.candidates import (
    EXPLICIT_MEMORY_TYPES,
    INFERRED_MEMORY_TYPES,
    MemoryCandidate,
)
from agent.memory.identity import preferences_key, validate_user_id
from agent.memory.preference_lock import (
    PreferenceLock,
    PreferenceLockUnavailable,
    lock_key,
)
from agent.memory.preferences import (
    CURRENT_SCHEMA_VERSION,
    MAX_RECENT_QUERIES,
    MAX_RECENT_SUPPLIERS,
    LoadedPreferences,
    PreferenceSchemaError,
    UserPreferences,
    default_preferences,
    dump_preferences,
    load_preferences,
    merge_recent_values,
    prune_recent,
)

EXPLICIT_FIELDS = (
    "preferred_output",
    "preferred_chart_type",
    "preferred_currency",
    "preferred_language",
)

_REASON_MESSAGES = {
    "read_failed": "偏好读取失败，本次未保存",
    "write_failed": "偏好写入失败，本次未保存",
    "invalid_schema": "偏好文件格式不合法，本次未保存",
    "invalid_request": "偏好参数不合法，本次未保存",
    "lock_timeout": "偏好正在被其他请求更新，本次未保存",
    "lock_unavailable": "偏好存储暂不可用，本次未保存",
    "conflict": "偏好版本冲突，本次未保存",
    "permission_denied": "无权写入该用户偏好",
    "not_found": "记忆不存在",
}


class PreferenceItemNotFound(Exception):
    """指定的 recent 项不存在。"""


@dataclass(frozen=True)
class MemoryCommitResult:
    ok: bool
    revision: int | None
    reason: str
    retryable: bool = False
    changed: bool = False
    message: str = ""
    applied_count: int = 0
    rejected_count: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)


def _failure(reason: str, *, retryable: bool = False) -> MemoryCommitResult:
    return MemoryCommitResult(
        ok=False,
        revision=None,
        reason=reason,
        retryable=retryable,
        message=_REASON_MESSAGES.get(reason, "偏好未保存"),
    )


class PreferenceMemoryService:
    def __init__(
        self,
        backend: Any,
        lock: PreferenceLock,
        *,
        retries: int = 3,
    ):
        self._backend = backend
        self._lock = lock
        self._retries = max(0, retries)

    async def update_auto(
        self,
        user_id: str,
        suppliers: list[str],
        query: str,
        *,
        request_id: str = "",
        thread_id: str = "",
    ) -> MemoryCommitResult:
        """兼容包装：把自动数据转成 inferred 候选后走统一白名单。"""
        source_id = request_id or "auto-memory"
        candidates = [
            MemoryCandidate(
                memory_type="recent_suppliers",
                value=str(supplier),
                origin="inferred",
                source_message_id=source_id,
                confidence=0.7,
                source="tool_result",
            )
            for supplier in suppliers
            if str(supplier).strip()
        ]
        if str(query or "").strip():
            candidates.append(
                MemoryCandidate(
                    memory_type="recent_queries",
                    value=str(query).strip(),
                    origin="inferred",
                    source_message_id=source_id,
                    confidence=0.7,
                    source="user_message",
                )
            )
        return await self.apply_candidates(
            user_id,
            candidates,
            request_id=request_id,
            thread_id=thread_id,
        )

    async def apply_candidates(
        self,
        user_id: str,
        candidates: Sequence[MemoryCandidate],
        *,
        request_id: str = "",
        thread_id: str = "",
    ) -> MemoryCommitResult:
        rejected: dict[str, int] = {}
        accepted: list[MemoryCandidate] = []
        explicit_values: dict[str, tuple[float, str]] = {}
        inferred_suppliers: list[str] = []
        inferred_queries: list[str] = []

        for candidate in candidates:
            if candidate.confidence < 0 or candidate.confidence > 1:
                rejected["invalid_confidence"] = rejected.get("invalid_confidence", 0) + 1
                continue
            if candidate.origin == "explicit":
                if candidate.memory_type not in EXPLICIT_MEMORY_TYPES:
                    rejected["invalid_origin"] = rejected.get("invalid_origin", 0) + 1
                    continue
                try:
                    self._validate_explicit_value(candidate.memory_type, candidate.value)
                except PreferenceSchemaError:
                    rejected["invalid_value"] = rejected.get("invalid_value", 0) + 1
                    continue
                current = explicit_values.get(candidate.memory_type)
                if current is None or candidate.confidence > current[0]:
                    explicit_values[candidate.memory_type] = (
                        candidate.confidence,
                        candidate.value,
                    )
                accepted.append(candidate)
            else:
                if candidate.memory_type not in INFERRED_MEMORY_TYPES:
                    rejected["invalid_origin"] = rejected.get("invalid_origin", 0) + 1
                    continue
                if candidate.confidence < 0.6:
                    rejected["low_confidence"] = rejected.get("low_confidence", 0) + 1
                    continue
                if not isinstance(candidate.value, str) or not candidate.value.strip():
                    rejected["empty_value"] = rejected.get("empty_value", 0) + 1
                    continue
                accepted.append(candidate)
                if candidate.memory_type == "recent_suppliers":
                    if candidate.value not in inferred_suppliers:
                        inferred_suppliers.append(candidate.value)
                elif candidate.value not in inferred_queries:
                    inferred_queries.append(candidate.value)

        if not accepted:
            return MemoryCommitResult(
                ok=True,
                revision=None,
                reason="noop",
                changed=False,
                applied_count=0,
                rejected_count=sum(rejected.values()),
                rejected_reasons=rejected,
            )

        def mutate(current: UserPreferences, migrated: bool):
            current = prune_recent(current)
            payload = current.model_dump(mode="json")
            for memory_type, (_, value) in explicit_values.items():
                payload[memory_type] = value

            now = datetime.now(timezone.utc)
            suppliers = merge_recent_values(
                current.recent_suppliers,
                inferred_suppliers,
                limit=MAX_RECENT_SUPPLIERS,
                now=now,
            )
            queries = merge_recent_values(
                current.recent_queries,
                inferred_queries,
                limit=MAX_RECENT_QUERIES,
                now=now,
            )
            payload["recent_suppliers"] = [
                item.model_dump(mode="json") for item in suppliers
            ]
            payload["recent_queries"] = [
                item.model_dump(mode="json") for item in queries
            ]
            try:
                candidate = UserPreferences.model_validate(payload)
            except ValidationError as exc:
                raise PreferenceSchemaError(
                    "invalid_request",
                    {str(error["loc"][0]): str(error["msg"]) for error in exc.errors()},
                ) from exc
            return candidate, candidate != current

        try:
            result = await self._commit(
                user_id,
                mutate,
                request_id=request_id,
                thread_id=thread_id,
                allow_create=True,
            )
        except PreferenceSchemaError:
            return _failure("invalid_request")
        return replace(
            result,
            applied_count=len(accepted),
            rejected_count=sum(rejected.values()),
            rejected_reasons=rejected,
        )

    async def get_preferences(self, user_id: str) -> UserPreferences:
        """只读快照；过期项在视图层过滤，不写 revision。"""
        current, reason = await self._read(user_id)
        if reason == "not_found":
            return default_preferences()
        if reason != "ok":
            raise PreferenceSchemaError("read_failed")
        assert current is not None
        return prune_recent(current.preferences)

    async def delete_recent_item(
        self,
        user_id: str,
        memory_type: str,
        value: str,
        *,
        request_id: str = "",
        thread_id: str = "",
    ) -> MemoryCommitResult:
        if memory_type not in {"recent_suppliers", "recent_queries"}:
            return _failure("invalid_request")
        if not isinstance(value, str) or not value.strip():
            return _failure("invalid_request")

        def mutate(current: UserPreferences, migrated: bool):
            current = prune_recent(current)
            payload = current.model_dump(mode="json")
            items = list(getattr(current, memory_type))
            remaining = [item for item in items if item.value != value]
            if len(remaining) == len(items):
                raise PreferenceItemNotFound(value)
            payload[memory_type] = [
                item.model_dump(mode="json") for item in remaining
            ]
            try:
                candidate = UserPreferences.model_validate(payload)
            except ValidationError as exc:
                raise PreferenceSchemaError(
                    "invalid_request",
                    {str(error["loc"][0]): str(error["msg"]) for error in exc.errors()},
                ) from exc
            return candidate, candidate != current

        try:
            return await self._commit(
                user_id,
                mutate,
                request_id=request_id,
                thread_id=thread_id,
                allow_create=False,
            )
        except PreferenceItemNotFound:
            return _failure("not_found")

    async def clear_all(
        self,
        user_id: str,
        *,
        request_id: str = "",
        thread_id: str = "",
    ) -> MemoryCommitResult:
        def mutate(current: UserPreferences, migrated: bool):
            payload = default_preferences().model_dump(mode="json")
            payload["revision"] = current.revision
            candidate = UserPreferences.model_validate(payload)
            return candidate, candidate != current

        return await self._commit(
            user_id,
            mutate,
            request_id=request_id,
            thread_id=thread_id,
            allow_create=False,
        )

    @staticmethod
    def _validate_explicit_value(memory_type: str, value: str | None) -> None:
        try:
            UserPreferences.model_validate(
                {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "revision": 0,
                    memory_type: value,
                }
            )
        except ValidationError as exc:
            raise PreferenceSchemaError(
                "invalid_request",
                {str(error["loc"][0]): str(error["msg"]) for error in exc.errors()},
            ) from exc

    async def save_explicit(
        self,
        user_id: str,
        updates: Mapping[str, Any],
        *,
        request_id: str = "",
        thread_id: str = "",
        source_message_id: str = "",
    ) -> MemoryCommitResult:
        normalized = dict(updates)
        unknown = set(normalized) - set(EXPLICIT_FIELDS)
        if not normalized or unknown:
            return _failure("invalid_request")

        for memory_type, value in normalized.items():
            try:
                self._validate_explicit_value(memory_type, value)
            except PreferenceSchemaError:
                return _failure("invalid_request")

        source_id = source_message_id or request_id or "explicit-tool"
        candidates = [
            MemoryCandidate(
                memory_type=field,
                value=value,
                origin="explicit",
                source_message_id=source_id,
                confidence=1.0,
                source="explicit_tool",
            )
            for field, value in normalized.items()
        ]
        return await self.apply_candidates(
            user_id,
            candidates,
            request_id=request_id,
            thread_id=thread_id,
        )

    async def _commit(
        self,
        user_id: str,
        mutate: Callable[[UserPreferences, bool], tuple[UserPreferences, bool]],
        *,
        request_id: str,
        thread_id: str,
        allow_create: bool,
    ) -> MemoryCommitResult:
        try:
            validate_user_id(user_id)
        except Exception:
            return _failure("permission_denied")

        key = lock_key(user_id)
        try:
            token = await self._lock.acquire(key)
        except PreferenceLockUnavailable:
            return _failure("lock_unavailable", retryable=True)
        if token is None:
            return _failure("lock_timeout", retryable=True)

        try:
            for _attempt in range(self._retries + 1):
                current, reason = await self._read(user_id)
                if reason == "read_failed":
                    return _failure("read_failed", retryable=True)
                if reason == "invalid_schema":
                    return _failure("invalid_schema")
                if current is None:
                    preferences = default_preferences()
                    migrated = False
                    existed = False
                else:
                    preferences = current.preferences
                    migrated = current.migrated
                    existed = True

                try:
                    candidate, changed = mutate(preferences, migrated)
                except PreferenceSchemaError:
                    return _failure("invalid_request")

                if not changed and not migrated:
                    return MemoryCommitResult(
                        ok=True,
                        revision=preferences.revision,
                        reason="noop",
                        changed=False,
                    )
                if not allow_create and not existed and not changed:
                    return MemoryCommitResult(
                        ok=True,
                        revision=preferences.revision,
                        reason="noop",
                        changed=False,
                    )

                # 写前再次读取：捕获非协作写入/上一轮冲突。
                check, check_reason = await self._read(user_id)
                if check_reason == "read_failed":
                    return _failure("read_failed", retryable=True)
                if check_reason == "invalid_schema":
                    return _failure("invalid_schema")
                if check_reason == "not_found":
                    if existed:
                        # 文件被并发删除：重新读取后再决定，不能直接覆盖。
                        continue
                    check = None
                if check is not None and check.preferences.revision != preferences.revision:
                    # 版本冲突：下一轮重新读取最新内容并重新合并。
                    continue

                base_revision = (
                    check.preferences.revision
                    if check is not None
                    else preferences.revision
                )
                candidate = candidate.model_copy(
                    update={
                        "schema_version": CURRENT_SCHEMA_VERSION,
                        "revision": base_revision + 1,
                    }
                )
                content = dump_preferences(candidate)
                try:
                    write_result = await self._backend.write_preferences_internal(
                        user_id,
                        preferences_key(user_id),
                        content,
                    )
                except Exception:
                    return _failure("write_failed", retryable=True)

                if getattr(write_result, "error", None):
                    return _failure("write_failed", retryable=True)

                result = MemoryCommitResult(
                    ok=True,
                    revision=candidate.revision,
                    reason="ok",
                    changed=True,
                    message="",
                )
                log_memory_event(
                    op="memory.commit",
                    user_id=user_id,
                    namespace=(user_id,),
                    path=preferences_key(user_id),
                    request_id=request_id,
                    thread_id=thread_id,
                    outcome="ok",
                    revision=result.revision,
                )
                return result

            return _failure("conflict", retryable=True)
        finally:
            await self._lock.release(key, token)

    async def _read(
        self, user_id: str
    ) -> tuple[LoadedPreferences | None, str]:
        path = preferences_key(user_id)
        try:
            result = await self._backend.aread(path)
        except Exception:
            return None, "read_failed"
        if result.error:
            if "not found" in result.error.lower():
                return None, "not_found"
            return None, "read_failed"

        file_data = result.file_data or {}
        content = (
            file_data.get("content", "")
            if isinstance(file_data, dict)
            else getattr(file_data, "content", "")
        )
        if isinstance(content, list):
            text = "\n".join(str(line) for line in content)
        else:
            text = str(content or "")
        try:
            return load_preferences(text), "ok"
        except PreferenceSchemaError:
            return None, "invalid_schema"
