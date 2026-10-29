"""用户偏好记忆的唯一 Pydantic 模型、解析、迁移与序列化。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CURRENT_SCHEMA_VERSION = 1

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


class UserPreferences(BaseModel):
    """偏好文件的结构化表示。

    strict + extra=forbid：不做隐式类型转换，未知字段直接拒绝。
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: int = CURRENT_SCHEMA_VERSION
    preferred_output: PreferredOutput | None = None
    preferred_chart_type: ChartType | None = None
    preferred_currency: Currency | None = None
    preferred_language: Language | None = None
    recent_suppliers: list[str] = Field(default_factory=list, max_length=10)
    recent_queries: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("schema_version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value < 1 or value > CURRENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version 必须为 1（当前版本 {CURRENT_SCHEMA_VERSION}）")
        return value

    @field_validator("recent_suppliers", "recent_queries")
    @classmethod
    def _validate_list_items(cls, values: list[str]) -> list[str]:
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"第 {index + 1} 项必须是非空字符串")
            if len(value) > 500:
                raise ValueError(f"第 {index + 1} 项超过 500 字符")
        return values


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
    if "schema_version" not in data:
        data["schema_version"] = CURRENT_SCHEMA_VERSION
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
        if version == 0:
            data["schema_version"] = CURRENT_SCHEMA_VERSION
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


def merge_auto_fields(
    preferences: UserPreferences,
    suppliers: list[str],
    query: str,
) -> UserPreferences:
    """只更新 recent_suppliers / recent_queries，显式偏好保持原值。"""
    incoming_suppliers: list[str] = []
    for supplier in suppliers:
        value = str(supplier).strip()[:500]
        if value and value not in incoming_suppliers:
            incoming_suppliers.append(value)

    merged_suppliers = list(incoming_suppliers)
    for supplier in preferences.recent_suppliers:
        if supplier not in merged_suppliers:
            merged_suppliers.append(supplier)
    merged_suppliers = merged_suppliers[:10]

    incoming_query = str(query or "").strip()[:500]
    merged_queries: list[str] = []
    if incoming_query:
        merged_queries.append(incoming_query)
    for existing in preferences.recent_queries:
        if existing not in merged_queries:
            merged_queries.append(existing)
    merged_queries = merged_queries[:5]

    payload = preferences.model_dump(mode="json")
    payload["recent_suppliers"] = merged_suppliers
    payload["recent_queries"] = merged_queries
    return UserPreferences.model_validate(payload)
