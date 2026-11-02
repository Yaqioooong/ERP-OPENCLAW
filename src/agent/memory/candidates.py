"""记忆候选模型：生成与持久化之间的唯一契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal[
    "preferred_output",
    "preferred_chart_type",
    "preferred_currency",
    "preferred_language",
    "recent_suppliers",
    "recent_queries",
]
Origin = Literal["explicit", "inferred"]
CandidateSource = Literal["user_message", "tool_result", "explicit_tool"]

EXPLICIT_MEMORY_TYPES = frozenset(
    {
        "preferred_output",
        "preferred_chart_type",
        "preferred_currency",
        "preferred_language",
    }
)
INFERRED_MEMORY_TYPES = frozenset({"recent_suppliers", "recent_queries"})


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_type: MemoryType
    value: str | None = Field(default=None, max_length=500)
    origin: Origin
    source_message_id: str = Field(min_length=1, max_length=200)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    confidence: float = Field(ge=0, le=1)
    source: CandidateSource
