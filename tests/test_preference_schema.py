from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.memory.preferences import (
    CURRENT_SCHEMA_VERSION,
    MAX_RECENT_QUERIES,
    MAX_RECENT_SUPPLIERS,
    PreferenceSchemaError,
    RecentMemoryItem,
    UserPreferences,
    default_preferences,
    dump_preferences,
    load_preferences,
    merge_auto_fields,
    prune_recent,
)

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)

VALID_YAML = """schema_version: 3
revision: 3
preferred_output: table
preferred_chart_type: bar
preferred_currency: CNY
preferred_language: zh
recent_suppliers:
  - value: 博世
    created_at: 2026-10-01T08:00:00+00:00
    last_seen_at: 2026-10-20T09:30:00+00:00
  - value: 大陆
    created_at: 2026-10-02T08:00:00+00:00
    last_seen_at: 2026-10-21T09:30:00+00:00
recent_queries:
  - value: 刹车片比价
    created_at: 2026-10-01T08:00:00+00:00
    last_seen_at: 2026-10-01T08:00:00+00:00
"""


def recent_values(items):
    return [item.value for item in items]


def test_valid_document_keeps_types_and_enums():
    loaded = load_preferences(VALID_YAML)
    prefs = loaded.preferences
    assert loaded.migrated is False
    assert prefs.schema_version == CURRENT_SCHEMA_VERSION
    assert prefs.revision == 3
    assert prefs.preferred_output == "table"
    assert prefs.preferred_chart_type == "bar"
    assert prefs.preferred_currency == "CNY"
    assert prefs.preferred_language == "zh"
    assert recent_values(prefs.recent_suppliers) == ["博世", "大陆"]
    assert isinstance(prefs.recent_suppliers[0], RecentMemoryItem)
    assert prefs.recent_suppliers[0].created_at.tzinfo is not None


def test_dump_roundtrip_and_version_first():
    prefs = load_preferences(VALID_YAML).preferences
    text = dump_preferences(prefs)
    assert text.splitlines()[0] == "schema_version: 3"
    assert load_preferences(text).preferences == prefs


def test_empty_document_uses_defaults():
    loaded = load_preferences("")
    assert loaded.migrated is False
    assert loaded.preferences == default_preferences()


def test_legacy_document_without_version_is_migrated():
    loaded = load_preferences(
        "preferred_output: table\nrecent_suppliers:\n  - 博世\n"
    )
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 3
    assert loaded.preferences.revision == 0
    assert loaded.preferences.preferred_output == "table"
    assert recent_values(loaded.preferences.recent_suppliers) == ["博世"]


def test_legacy_zero_version_is_migrated():
    loaded = load_preferences("schema_version: 0\npreferred_output: chart\n")
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 3
    assert loaded.preferences.revision == 0
    assert loaded.preferences.preferred_output == "chart"


def test_v1_is_migrated_with_revision_zero():
    loaded = load_preferences(
        "schema_version: 1\npreferred_output: table\npreferred_currency: USD\n"
    )
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 3
    assert loaded.preferences.revision == 0
    assert loaded.preferences.preferred_output == "table"
    assert loaded.preferences.preferred_currency == "USD"


def test_v2_string_lists_migrate_to_timed_items():
    loaded = load_preferences(
        "schema_version: 2\nrevision: 4\nrecent_suppliers:\n  - 博世\n"
    )
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 3
    assert loaded.preferences.revision == 4
    item = loaded.preferences.recent_suppliers[0]
    assert item.value == "博世"
    assert item.created_at == item.last_seen_at


def test_future_version_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 4\n")
    assert exc.value.reason == "unsupported_version"


def test_schema_version_type_is_strict():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: '3'\n")
    assert exc.value.reason == "invalid_schema_version"


def test_invalid_yaml_indentation_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 3\npreferred_output: table\n  bad: x\n")
    assert exc.value.reason == "yaml_error"


def test_invalid_enum_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 3\npreferred_output: pie\n")
    assert exc.value.reason == "invalid_enum"
    assert "preferred_output" in exc.value.field_errors


def test_wrong_field_type_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 3\nrecent_suppliers: not-a-list\n")
    assert exc.value.reason == "invalid_structure"
    assert "recent_suppliers" in exc.value.field_errors


def test_unknown_field_is_rejected_not_dropped():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 3\nfoo: bar\n")
    assert exc.value.reason == "unknown_field"
    assert "foo" in exc.value.field_errors


def test_top_level_must_be_mapping():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("- a\n- b\n")
    assert exc.value.reason == "invalid_structure"


def _items(prefix: str, count: int):
    return [
        {
            "value": f"{prefix}{i}",
            "created_at": NOW.isoformat(),
            "last_seen_at": NOW.isoformat(),
        }
        for i in range(count)
    ]


def test_list_length_limit_is_rejected():
    import yaml

    text = yaml.safe_dump(
        {
            "schema_version": 3,
            "recent_suppliers": _items("s", MAX_RECENT_SUPPLIERS + 1),
        },
        allow_unicode=True,
    )
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences(text)
    assert "recent_suppliers" in exc.value.field_errors


def test_merge_preserves_explicit_fields_and_dedupes():
    prefs = load_preferences(VALID_YAML).preferences
    merged = merge_auto_fields(prefs, ["博世", "新供应商", "新供应商"], "新的比价")
    assert merged.preferred_output == "table"
    assert merged.preferred_chart_type == "bar"
    assert merged.preferred_currency == "CNY"
    assert merged.preferred_language == "zh"
    assert recent_values(merged.recent_suppliers) == ["博世", "新供应商", "大陆"]
    assert recent_values(merged.recent_queries) == ["新的比价", "刹车片比价"]
    bosch = next(item for item in merged.recent_suppliers if item.value == "博世")
    assert bosch.created_at < bosch.last_seen_at


def test_merge_respects_limits_and_keeps_newest():
    prefs = UserPreferences(
        recent_suppliers=[
            RecentMemoryItem(
                value=f"s{i}", created_at=NOW, last_seen_at=NOW
            )
            for i in range(MAX_RECENT_SUPPLIERS)
        ],
        recent_queries=[
            RecentMemoryItem(
                value=f"q{i}", created_at=NOW, last_seen_at=NOW
            )
            for i in range(MAX_RECENT_QUERIES)
        ],
    )
    merged = merge_auto_fields(prefs, ["new"], "new-query")
    assert len(merged.recent_suppliers) == MAX_RECENT_SUPPLIERS
    assert recent_values(merged.recent_suppliers)[0] == "new"
    assert len(merged.recent_queries) == MAX_RECENT_QUERIES
    assert recent_values(merged.recent_queries)[0] == "new-query"


def test_prune_recent_by_ttl():
    old = NOW - timedelta(days=100)
    recent = NOW - timedelta(days=10)
    prefs = UserPreferences(
        recent_suppliers=[
            RecentMemoryItem(value="old", created_at=old, last_seen_at=old),
            RecentMemoryItem(value="new", created_at=recent, last_seen_at=recent),
        ],
        recent_queries=[
            RecentMemoryItem(
                value="old-query",
                created_at=NOW - timedelta(days=40),
                last_seen_at=NOW - timedelta(days=40),
            )
        ],
    )
    pruned = prune_recent(prefs, now=NOW)
    assert recent_values(pruned.recent_suppliers) == ["new"]
    assert pruned.recent_queries == []


def test_model_strictly_rejects_implicit_conversion():
    with pytest.raises(Exception):
        UserPreferences(preferred_output="table", schema_version="3")
    with pytest.raises(Exception):
        UserPreferences(unknown_field="x")


def test_chart_type_enum_matches_generator_guide():
    from typing import get_args

    from agent.tools.chart_generator import CHART_TYPE_GUIDE
    from agent.memory.preferences import ChartType

    assert set(get_args(ChartType)) == set(CHART_TYPE_GUIDE)


def test_schema_reexports_the_same_model():
    from agent.schema import UserPreferences as SchemaPreferences
    from agent.memory.preferences import UserPreferences as MemoryPreferences

    assert SchemaPreferences is MemoryPreferences
