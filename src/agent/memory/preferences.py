"""用户偏好记忆的唯一 Pydantic 模型、解析、迁移与序列化。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CURRENT_SCHEMA_VERSION = 3

RECENT_SUPPLIER_TTL_DAYS = int(os.getenv("PREFERENCE_SUPPLIER_TTL_DAYS", "90"))
RECENT_QUERY_TTL_DAYS = int(os.getenv("PREFERENCE_QUERY_TTL_DAYS", "30"))
MAX_RECENT_SUPPLIERS = 10
MAX_RECENT_QUERIES = 5

PreferredOutput = Literal["table", "chart"]
Currency = Literal["CNY", "USD", "EUR", "JPY", "HKD", "GBP"]
Language = Literal["zh", "en"]

# 与 src/agent/tools/chart_generator.py 的 CHART_TYPE_GUIDE 保持同步。
ChartType = Literal[
    "area",
    "bar",
    "boxplot",
    "column",
    "dual_axes",
    "funnel",
    "histogram",
    "line",
    "liquid",
    "organization",
    "pie",
    "radar",
    "sankey",
    "scatter",
    "treemap",
    "venn",
    "violin",
    "waterfall",
    "word_cloud",
    "district_map",
    "path_map",
    "pin_map",
    "fishbone_diagram",
    "flow_diagram",
    "network_graph",
    "mind_map",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecentMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=500)
    created_at: datetime
    last_seen_at: datetime

    @field_validator("created_at", "last_seen_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("last_seen_at")
    @classmethod
    def _not_before_created(cls, value: datetime, info) -> datetime:
        created = info.data.get("created_at")
        if created is not None and value < created:
            raise ValueError("last_seen_at 不能早于 created_at")
        return value


class UserPreferences(BaseModel):
    """偏好文件的结构化表示。

    strict + extra=forbid：不做隐式类型转换，未知字段直接拒绝。
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: int = CURRENT_SCHEMA_VERSION
    revision: int = 0
    preferred_output: PreferredOutput | None = None
    preferred_chart_type: ChartType | None = None
    preferred_currency: Currency | None = None
    preferred_language: Language | None = None
    recent_suppliers: list[RecentMemoryItem] = Field(
        default_factory=list, max_length=MAX_RECENT_SUPPLIERS
    )
    recent_queries: list[RecentMemoryItem] = Field(
        default_factory=list, max_length=MAX_RECENT_QUERIES
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version 必须为 {CURRENT_SCHEMA_VERSION}")
        return value

    @field_validator("revision")
    @classmethod
    def _validate_revision(cls, value: int) -> int:
        if value < 0:
            raise ValueError("revision 不能为负数")
        return value

    @field_validator("recent_suppliers", "recent_queries")
    @classmethod
    def _unique_values(cls, items: list[RecentMemoryItem]):
        values = [item.value for item in items]
        if len(values) != len(set(values)):
            raise ValueError("recent 记忆存在重复 value")
        return items


class PreferenceSchemaError(ValueError):
    """偏好文件无法安全读取；调用方不得写回。"""

    def __init__(
        self,
        reason: str,
        field_errors: dict[str, str] | None = None,
        *,
        detail: str = "",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.field_errors = field_errors or {}
        self.detail = detail


@dataclass(frozen=True)
class LoadedPreferences:
    preferences: UserPreferences
    migrated: bool = False
    field_errors: dict[str, str] = field(default_factory=dict)


def default_preferences() -> UserPreferences:
    return UserPreferences()


def _error_field(loc: tuple) -> str:
    parts = []
    for part in loc:
        if isinstance(part, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(str(part))
    return ".".join(parts)


def _validation_error(exc: ValidationError) -> PreferenceSchemaError:
    field_errors: dict[str, str] = {}
    has_unknown = False
    has_enum = False
    for error in exc.errors():
        field_name = _error_field(tuple(error.get("loc", ()))) or "<root>"
        error_type = error.get("type", "")
        if error_type == "extra_forbidden":
            has_unknown = True
            field_errors[field_name] = "未知字段，拒绝写入"
        elif error_type == "literal_error":
            has_enum = True
            field_errors[field_name] = "不在允许的枚举值范围内"
        else:
            field_errors[field_name] = str(error.get("msg", "字段校验失败"))
    reason = (
        "unknown_field"
        if has_unknown
        else "invalid_enum"
        if has_enum
        else "invalid_structure"
    )
    return PreferenceSchemaError(reason, field_errors)


def _migrate_recent(value: Any, *, field_name: str, now: datetime) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PreferenceSchemaError(
            "invalid_structure", {field_name: "必须是列表"}
        )
    migrated: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            if not item.strip():
                raise PreferenceSchemaError(
                    "invalid_structure", {f"{field_name}[{index}]": "不能为空"}
                )
            migrated.append(
                {
                    "value": item,
                    "created_at": now,
                    "last_seen_at": now,
                }
            )
        elif isinstance(item, dict):
            migrated.append(item)
        else:
            raise PreferenceSchemaError(
                "invalid_structure", {f"{field_name}[{index}]": "非法条目"}
            )
    return migrated


def load_preferences(text: str | None) -> LoadedPreferences:
    """解析偏好文件；任何异常都以 PreferenceSchemaError 失败关闭。"""
    if text is None or not text.strip():
        return LoadedPreferences(preferences=default_preferences())

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PreferenceSchemaError("yaml_error", {"yaml": "YAML 解析失败"}) from exc

    if raw is None:
        return LoadedPreferences(preferences=default_preferences())
    if not isinstance(raw, dict):
        raise PreferenceSchemaError("invalid_structure", {"<root>": "顶层必须是对象"})

    data: dict[str, Any] = dict(raw)
    migrated = False
    now = _utc_now()

    if "schema_version" not in data:
        data["schema_version"] = CURRENT_SCHEMA_VERSION
        data["revision"] = 0
        data["recent_suppliers"] = _migrate_recent(
            data.get("recent_suppliers"),
            field_name="recent_suppliers",
            now=now,
        )
        data["recent_queries"] = _migrate_recent(
            data.get("recent_queries"),
            field_name="recent_queries",
            now=now,
        )
        migrated = True
    else:
        version = data["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise PreferenceSchemaError(
                "invalid_schema_version", {"schema_version": "版本号必须是整数"}
            )
        if version > CURRENT_SCHEMA_VERSION:
            raise PreferenceSchemaError(
                "unsupported_version",
                {"schema_version": f"不支持版本 {version}，当前仅支持 {CURRENT_SCHEMA_VERSION}"},
            )
        if version < 0:
            raise PreferenceSchemaError(
                "invalid_schema_version", {"schema_version": "版本号不能为负数"}
            )
        if version < CURRENT_SCHEMA_VERSION:
            if version in (0, 1):
                data["revision"] = 0
            data["schema_version"] = CURRENT_SCHEMA_VERSION
            data["recent_suppliers"] = _migrate_recent(
                data.get("recent_suppliers"),
                field_name="recent_suppliers",
                now=now,
            )
            data["recent_queries"] = _migrate_recent(
                data.get("recent_queries"),
                field_name="recent_queries",
                now=now,
            )
            migrated = True

    try:
        preferences = UserPreferences.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    return LoadedPreferences(preferences=preferences, migrated=migrated)


def dump_preferences(preferences: UserPreferences) -> str:
    """稳定、可回读的 YAML；schema_version 位于文件开头。"""
    payload = preferences.model_dump(mode="json")
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _merge_recent(
    existing: list[RecentMemoryItem],
    incoming: list[str],
    *,
    now: datetime,
    limit: int,
) -> list[RecentMemoryItem]:
    normalized: list[str] = []
    for value in incoming:
        text = str(value).strip()[:500]
        if text and text not in normalized:
            normalized.append(text)

    by_value = {item.value: item for item in existing}
    result: list[RecentMemoryItem] = []
    for value in normalized:
        current = by_value.get(value)
        if current is None:
            result.append(
                RecentMemoryItem(
                    value=value,
                    created_at=now,
                    last_seen_at=now,
                )
            )
        else:
            result.append(
                current.model_copy(update={"last_seen_at": now})
            )

    for item in existing:
        if item.value not in normalized:
            result.append(item)
    return result[:limit]


def merge_auto_fields(
    preferences: UserPreferences,
    suppliers: list[str],
    query: str,
) -> UserPreferences:
    """只更新 recent 条目；显式偏好与 revision 保持。"""
    now = _utc_now()
    suppliers_merged = _merge_recent(
        preferences.recent_suppliers,
        suppliers,
        now=now,
        limit=MAX_RECENT_SUPPLIERS,
    )
    query_values = [query] if str(query or "").strip() else []
    queries_merged = _merge_recent(
        preferences.recent_queries,
        query_values,
        now=now,
        limit=MAX_RECENT_QUERIES,
    )

    payload = preferences.model_dump(mode="json")
    payload["recent_suppliers"] = [
        item.model_dump(mode="json") for item in suppliers_merged
    ]
    payload["recent_queries"] = [
        item.model_dump(mode="json") for item in queries_merged
    ]
    return UserPreferences.model_validate(payload)


def prune_recent(
    preferences: UserPreferences,
    *,
    now: datetime | None = None,
) -> UserPreferences:
    """按 last_seen_at 淘汰过期 recent 项；不改变显式字段/revision。"""
    now = now or _utc_now()
    supplier_cutoff = now - timedelta(days=RECENT_SUPPLIER_TTL_DAYS)
    query_cutoff = now - timedelta(days=RECENT_QUERY_TTL_DAYS)
    payload = preferences.model_dump(mode="json")
    payload["recent_suppliers"] = [
        item.model_dump(mode="json")
        for item in preferences.recent_suppliers
        if item.last_seen_at >= supplier_cutoff
    ]
    payload["recent_queries"] = [
        item.model_dump(mode="json")
        for item in preferences.recent_queries
        if item.last_seen_at >= query_cutoff
    ]
    return UserPreferences.model_validate(payload)


def recent_values(items: list[RecentMemoryItem]) -> list[str]:
    return [item.value for item in items]


def merge_recent_values(
    existing: list[RecentMemoryItem],
    values: list[str],
    *,
    limit: int,
    now: datetime | None = None,
) -> list[RecentMemoryItem]:
    """合并新值并刷新 last_seen；供服务层显式合并使用。"""
    return _merge_recent(
        existing,
        values,
        now=now or _utc_now(),
        limit=limit,
    )
