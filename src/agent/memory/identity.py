"""可信用户身份与记忆路径规则。

身份只能来自服务端认证后的 ``ProcurementContext``；这里负责在记忆链路
上做最后一道校验，任何缺失或非法身份都必须直接失败，禁止回退到默认用户。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

# user_id 会进入 Store 命名空间与记忆路径，限制为可安全拼接的字符集。
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

MEMORY_ROOT = "/memories"


class MemoryIdentityError(RuntimeError):
    """身份缺失或非法，记忆操作必须拒绝。"""

    def __init__(self, reason: str = "missing_identity") -> None:
        super().__init__(reason)
        self.reason = reason


class MemoryPathError(MemoryIdentityError):
    """路径不属于当前用户，或包含穿越/编码变体。"""

    def __init__(self, reason: str = "path_not_owned", path: str = "") -> None:
        super().__init__(reason)
        self.path = path


def validate_user_id(user_id: Any) -> str:
    """校验并返回可信 user_id；非法值直接抛错，不做任何兜底。"""
    if not isinstance(user_id, str) or not USER_ID_PATTERN.fullmatch(user_id):
        raise MemoryIdentityError("invalid_identity")
    return user_id


def require_user_id(context: Any) -> str:
    """从运行时上下文中读取 user_id，缺失时拒绝。"""
    if isinstance(context, dict):
        user_id = context.get("user_id")
    else:
        user_id = getattr(context, "user_id", None)
    if user_id is None or user_id == "":
        raise MemoryIdentityError("missing_identity")
    return validate_user_id(user_id)


def memory_namespace(user_id: str) -> tuple[str, ...]:
    """用户记忆的唯一合法命名空间。"""
    return (validate_user_id(user_id),)


def user_namespace_for_runtime(runtime: Any) -> tuple[str, ...]:
    """StoreBackend 命名空间工厂：身份不可用时抛异常而不是回退。"""
    return memory_namespace(require_user_id(getattr(runtime, "context", None)))


def preferences_key(user_id: str) -> str:
    """Store 内部 key（/memories 前缀已被 CompositeBackend 剥离）。"""
    return f"/{validate_user_id(user_id)}/preferences.md"


def preferences_path(user_id: str) -> str:
    """Agent/中间件可见的 /memories 路径。"""
    return f"{MEMORY_ROOT}/{validate_user_id(user_id)}/preferences.md"


def _segments(path: str) -> list[str]:
    return path.split("/")[1:]


def _reject_traversal(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise MemoryPathError("invalid_path", path if isinstance(path, str) else "")
    if "\x00" in path or "\\" in path:
        raise MemoryPathError("path_traversal", path)
    for candidate in {path, unquote(path)}:
        normalized = (
            candidate[:-1]
            if candidate.endswith("/") and candidate != "/"
            else candidate
        )
        parts = _segments(normalized)
        if any(part in ("", ".", "..") for part in parts):
            raise MemoryPathError("path_traversal", path)


def validate_memory_path(
    user_id: str,
    path: str,
    *,
    allow_root: bool = False,
) -> str:
    """校验剥离 /memories 前缀后的 Store key 属于当前用户。

    ``allow_root`` 用于 ls/grep/glob 等目录级操作：``/`` 与 ``/{user_id}``
    可以访问，但范围仍由 ``(user_id,)`` 命名空间限制。
    """
    user_id = validate_user_id(user_id)

    if allow_root and path in ("/", f"/{user_id}", f"/{user_id}/"):
        if "\x00" in path or "\\" in path:
            raise MemoryPathError("path_traversal", path)
        return path

    _reject_traversal(path)

    prefix = f"/{user_id}/"
    if not path.startswith(prefix) or path == prefix:
        raise MemoryPathError("path_not_owned", path)
    return path


def validate_search_path(user_id: str, path: str | None) -> str | None:
    """grep/glob 的 path 允许 None 或根目录。"""
    if path is None:
        return None
    if path in ("", "/"):
        return "/"
    return validate_memory_path(user_id, path, allow_root=True)
