"""带身份与路径校验、审计日志的用户记忆 Backend。

包装 ``StoreBackend``：命名空间固定为 ``(user_id,)``，且所有路径必须是
``/{user_id}/...``。校验失败的请求不会触达底层 Store。
"""
from __future__ import annotations

from typing import Any

from deepagents.backends import StoreBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from langgraph.runtime import get_runtime

from agent.memory.audit import log_memory_event
from agent.memory.identity import (
    MemoryPathError,
    validate_memory_path,
    validate_search_path,
)


PREFERENCE_FILENAME = "preferences.md"


def is_preferences_path(path: str) -> bool:
    return str(path).rstrip("/").endswith(f"/{PREFERENCE_FILENAME}")


class UserMemoryBackend(StoreBackend):
    """只允许当前可信用户访问自己的记忆命名空间。"""

    def __init__(self, *, store: Any, namespace: Any):
        super().__init__(store=store, namespace=namespace)

    # ---- 身份 / 路径 / 日志 ----
    def _runtime_meta(self) -> tuple[str, str]:
        try:
            runtime = get_runtime()
            context = getattr(runtime, "context", None)
        except Exception:
            context = None
        request_id = getattr(context, "request_id", "") or ""
        thread_id = getattr(context, "thread_id", "") or ""
        return str(request_id), str(thread_id)

    def _current_user(self) -> str:
        namespace = self._get_namespace()
        return namespace[0]

    def _deny(self, user_id: str, op: str, path: str, reason: str) -> None:
        request_id, thread_id = self._runtime_meta()
        log_memory_event(
            op=f"memory.{op}",
            user_id=user_id,
            namespace=(user_id,),
            path=path,
            request_id=request_id,
            thread_id=thread_id,
            outcome="denied",
            reason=reason,
        )

    def _check_path(
        self,
        user_id: str,
        op: str,
        path: str,
        *,
        allow_root: bool = False,
    ) -> bool:
        try:
            validate_memory_path(user_id, path, allow_root=allow_root)
        except MemoryPathError as exc:
            self._deny(user_id, op, path, exc.reason)
            return False
        return True

    def _check_search_path(self, user_id: str, op: str, path: str | None) -> bool:
        try:
            validate_search_path(user_id, path)
        except MemoryPathError as exc:
            self._deny(user_id, op, path or "", exc.reason)
            return False
        return True

    def _log(
        self,
        user_id: str,
        op: str,
        path: str,
        error: str | None,
        **extra: Any,
    ) -> None:
        request_id, thread_id = self._runtime_meta()
        log_memory_event(
            op=f"memory.{op}",
            user_id=user_id,
            namespace=(user_id,),
            path=path,
            request_id=request_id,
            thread_id=thread_id,
            outcome="ok" if not error else "error",
            reason=error or "",
            **extra,
        )

    # ---- 读 ----
    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "read", file_path):
            return ReadResult(error="permission_denied")
        result = super().read(file_path, offset=offset, limit=limit)
        self._log(user_id, "read", file_path, result.error)
        return result

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "read", file_path):
            return ReadResult(error="permission_denied")
        result = await super().aread(file_path, offset=offset, limit=limit)
        self._log(user_id, "read", file_path, result.error)
        return result

    # ---- 写 ----
    def write(self, file_path: str, content: str) -> WriteResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "write", file_path):
            return WriteResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "write", file_path, "preference_write_requires_tool")
            return WriteResult(error="use_save_preferences_tool")
        result = super().write(file_path, content)
        self._log(user_id, "write", file_path, result.error, size=len(content or ""))
        return result

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "write", file_path):
            return WriteResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "write", file_path, "preference_write_requires_tool")
            return WriteResult(error="use_save_preferences_tool")
        result = await super().awrite(file_path, content)
        self._log(user_id, "write", file_path, result.error, size=len(content or ""))
        return result

    # ---- 编辑 ----
    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "edit", file_path):
            return EditResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "edit", file_path, "preference_write_requires_tool")
            return EditResult(error="use_save_preferences_tool")
        result = super().edit(
            file_path, old_string, new_string, replace_all=replace_all
        )
        self._log(user_id, "edit", file_path, result.error)
        return result

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "edit", file_path):
            return EditResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "edit", file_path, "preference_write_requires_tool")
            return EditResult(error="use_save_preferences_tool")
        result = await super().aedit(
            file_path, old_string, new_string, replace_all=replace_all
        )
        self._log(user_id, "edit", file_path, result.error)
        return result

    # ---- 删除 ----
    def delete(self, file_path: str) -> DeleteResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "delete", file_path):
            return DeleteResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "delete", file_path, "preference_write_requires_tool")
            return DeleteResult(error="use_save_preferences_tool")
        result = super().delete(file_path)
        self._log(user_id, "delete", file_path, result.error)
        return result

    async def adelete(self, file_path: str) -> DeleteResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "delete", file_path):
            return DeleteResult(error="permission_denied")
        if is_preferences_path(file_path):
            self._deny(user_id, "delete", file_path, "preference_write_requires_tool")
            return DeleteResult(error="use_save_preferences_tool")
        result = await super().adelete(file_path)
        self._log(user_id, "delete", file_path, result.error)
        return result

    # ---- 目录 ----
    def ls(self, path: str) -> LsResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "ls", path, allow_root=True):
            return LsResult(error="permission_denied")
        result = super().ls(path)
        self._log(user_id, "ls", path, result.error)
        return result

    async def als(self, path: str) -> LsResult:
        user_id = self._current_user()
        if not self._check_path(user_id, "ls", path, allow_root=True):
            return LsResult(error="permission_denied")
        result = await super().als(path)
        self._log(user_id, "ls", path, result.error)
        return result

    # ---- 搜索 ----
    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        user_id = self._current_user()
        if not self._check_search_path(user_id, "grep", path):
            return GrepResult(error="permission_denied")
        result = super().grep(pattern, path, glob, max_count=max_count)
        self._log(user_id, "grep", path or "/", result.error)
        return result

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        user_id = self._current_user()
        if not self._check_search_path(user_id, "grep", path):
            return GrepResult(error="permission_denied")
        result = await super().agrep(pattern, path, glob, max_count=max_count)
        self._log(user_id, "grep", path or "/", result.error)
        return result

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        user_id = self._current_user()
        if ".." in (pattern or ""):
            self._deny(user_id, "glob", path or "/", "path_traversal")
            return GlobResult(error="permission_denied")
        if not self._check_search_path(user_id, "glob", path):
            return GlobResult(error="permission_denied")
        result = super().glob(pattern, path)
        self._log(user_id, "glob", path or "/", result.error)
        return result

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        user_id = self._current_user()
        if ".." in (pattern or ""):
            self._deny(user_id, "glob", path or "/", "path_traversal")
            return GlobResult(error="permission_denied")
        if not self._check_search_path(user_id, "glob", path):
            return GlobResult(error="permission_denied")
        result = await super().aglob(pattern, path)
        self._log(user_id, "glob", path or "/", result.error)
        return result

    async def write_preferences_internal(
        self,
        user_id: str,
        file_path: str,
        content: str,
    ) -> WriteResult:
        """仅供 PreferenceMemoryService 在锁内调用，绕过通用写入限制。"""
        current_user = self._current_user()
        if current_user != user_id:
            self._deny(current_user, "write", file_path, "identity_mismatch")
            return WriteResult(error="permission_denied")
        if not self._check_path(user_id, "write", file_path):
            return WriteResult(error="permission_denied")
        if not is_preferences_path(file_path):
            return WriteResult(error="invalid_path")
        result = await super().awrite(file_path, content)
        self._log(user_id, "write", file_path, result.error, size=len(content or ""))
        return result

    # ---- 上传 / 下载 ----
    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        user_id = self._current_user()
        responses: list[FileUploadResponse] = []
        accepted: list[tuple[str, bytes]] = []
        for path, content in files:
            if self._check_path(user_id, "write", path) and not is_preferences_path(path):
                accepted.append((path, content))
            else:
                if is_preferences_path(path):
                    self._deny(user_id, "write", path, "preference_write_requires_tool")
                    responses.append(
                        FileUploadResponse(path=path, error="use_save_preferences_tool")
                    )
                else:
                    responses.append(
                        FileUploadResponse(path=path, error="permission_denied")
                    )
        responses.extend(super().upload_files(accepted))
        return responses

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        user_id = self._current_user()
        responses: list[FileUploadResponse] = []
        accepted: list[tuple[str, bytes]] = []
        for path, content in files:
            if self._check_path(user_id, "write", path) and not is_preferences_path(path):
                accepted.append((path, content))
            else:
                if is_preferences_path(path):
                    self._deny(user_id, "write", path, "preference_write_requires_tool")
                    responses.append(
                        FileUploadResponse(path=path, error="use_save_preferences_tool")
                    )
                else:
                    responses.append(
                        FileUploadResponse(path=path, error="permission_denied")
                    )
        responses.extend(await super().aupload_files(accepted))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        user_id = self._current_user()
        responses: list[FileDownloadResponse] = []
        accepted: list[str] = []
        for path in paths:
            if self._check_path(user_id, "read", path):
                accepted.append(path)
            else:
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
        responses.extend(super().download_files(accepted))
        return responses

    async def adownload_files(
        self, paths: list[str]
    ) -> list[FileDownloadResponse]:
        user_id = self._current_user()
        responses: list[FileDownloadResponse] = []
        accepted: list[str] = []
        for path in paths:
            if self._check_path(user_id, "read", path):
                accepted.append(path)
            else:
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
        responses.extend(await super().adownload_files(accepted))
        return responses
