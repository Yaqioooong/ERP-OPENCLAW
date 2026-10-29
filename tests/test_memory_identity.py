from __future__ import annotations

import pytest

from agent.memory.identity import (
    MemoryIdentityError,
    MemoryPathError,
    memory_namespace,
    preferences_key,
    preferences_path,
    require_user_id,
    validate_memory_path,
    validate_search_path,
    validate_user_id,
)
from agent.schema import ProcurementContext


def test_valid_user_id_and_namespace():
    assert validate_user_id("u_1001") == "u_1001"
    assert memory_namespace("u_1001") == ("u_1001",)
    assert preferences_key("u_1001") == "/u_1001/preferences.md"
    assert preferences_path("u_1001") == "/memories/u_1001/preferences.md"


@pytest.mark.parametrize("user_id", ["", None, "..", "../x", "张三", "a/b", "a" * 65])
def test_invalid_user_id_is_rejected(user_id):
    with pytest.raises(MemoryIdentityError):
        validate_user_id(user_id)


def test_require_user_id_does_not_fall_back():
    with pytest.raises(MemoryIdentityError):
        require_user_id(None)
    with pytest.raises(MemoryIdentityError):
        require_user_id(type("Ctx", (), {"user_id": ""})())


def test_procurement_context_requires_user_id():
    with pytest.raises(ValueError):
        ProcurementContext(user_id="", username="张三")
    context = ProcurementContext(user_id="u_1001", username="张三")
    assert context.user_id == "u_1001"


def test_memory_path_must_be_owned():
    assert (
        validate_memory_path("u_1001", "/u_1001/preferences.md")
        == "/u_1001/preferences.md"
    )
    with pytest.raises(MemoryPathError) as exc:
        validate_memory_path("u_1001", "/u_1002/preferences.md")
    assert exc.value.reason == "path_not_owned"


@pytest.mark.parametrize(
    "path",
    [
        "/u_1001/../u_1002/preferences.md",
        "/u_1001//preferences.md",
        "/u_1001/./preferences.md",
        "/u_1001/%2e%2e/u_1002/preferences.md",
        "/u_1001\\..\\u_1002\\preferences.md",
        "/u_1001/preferences.md\x00",
        "u_1001/preferences.md",
    ],
)
def test_traversal_and_malformed_paths_are_rejected(path):
    with pytest.raises(MemoryPathError):
        validate_memory_path("u_1001", path)


def test_search_path_allows_only_root_or_own_subtree():
    assert validate_search_path("u_1001", None) is None
    assert validate_search_path("u_1001", "/") == "/"
    assert validate_search_path("u_1001", "/u_1001") == "/u_1001"
    assert (
        validate_search_path("u_1001", "/u_1001/sub")
        == "/u_1001/sub"
    )
    with pytest.raises(MemoryPathError):
        validate_search_path("u_1001", "/u_1002")


def test_memory_chain_modules_have_no_demo_identity_fallback():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    files = [
        "src/api_view/api/chat.py",
        "src/api_view/agent_loader.py",
        "src/agent/main_agent.py",
        "src/agent/schema.py",
        "src/agent/middlewares/memory_update.py",
        "src/agent/middlewares/context_injection.py",
        "src/agent/tools/procurement_task_tools.py",
        "src/agent/memory/identity.py",
        "src/agent/memory/audit.py",
        "src/agent/memory/user_memory_backend.py",
    ]
    offenders = [
        path
        for path in files
        if "guoyaxing" in (root / path).read_text(encoding="utf-8")
    ]
    assert offenders == []
