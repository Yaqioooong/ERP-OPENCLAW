from __future__ import annotations

import pytest

from agent.memory.preferences import (
    CURRENT_SCHEMA_VERSION,
    PreferenceSchemaError,
    UserPreferences,
    default_preferences,
    dump_preferences,
    load_preferences,
    merge_auto_fields,
)

VALID_YAML = """schema_version: 1
preferred_output: table
preferred_chart_type: bar
preferred_currency: CNY
preferred_language: zh
recent_suppliers:
  - 博世
  - 大陆
recent_queries:
  - 刹车片比价
"""


def test_valid_document_keeps_types_and_enums():
    loaded = load_preferences(VALID_YAML)
    prefs = loaded.preferences
    assert loaded.migrated is False
    assert prefs.schema_version == CURRENT_SCHEMA_VERSION
    assert prefs.preferred_output == "table"
    assert prefs.preferred_chart_type == "bar"
    assert prefs.preferred_currency == "CNY"
    assert prefs.preferred_language == "zh"
    assert prefs.recent_suppliers == ["博世", "大陆"]
    assert isinstance(prefs.recent_suppliers, list)


def test_dump_roundtrip_and_version_first():
    prefs = load_preferences(VALID_YAML).preferences
    text = dump_preferences(prefs)
    assert text.splitlines()[0] == "schema_version: 1"
    assert load_preferences(text).preferences == prefs


def test_empty_document_uses_defaults():
    loaded = load_preferences("")
    assert loaded.migrated is False
    assert loaded.preferences == default_preferences()


def test_legacy_document_without_version_is_migrated():
    loaded = load_preferences("preferred_output: table\nrecent_suppliers: []\n")
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 1
    assert loaded.preferences.preferred_output == "table"


def test_legacy_zero_version_is_migrated():
    loaded = load_preferences("schema_version: 0\npreferred_output: chart\n")
    assert loaded.migrated is True
    assert loaded.preferences.schema_version == 1
    assert loaded.preferences.preferred_output == "chart"


def test_future_version_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 2\n")
    assert exc.value.reason == "unsupported_version"


def test_schema_version_type_is_strict():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: '1'\n")
    assert exc.value.reason == "invalid_schema_version"


def test_invalid_yaml_indentation_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 1\npreferred_output: table\n  bad: x\n")
    assert exc.value.reason == "yaml_error"


def test_invalid_enum_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 1\npreferred_output: pie\n")
    assert exc.value.reason == "invalid_enum"
    assert "preferred_output" in exc.value.field_errors


def test_wrong_field_type_is_rejected():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 1\nrecent_suppliers: not-a-list\n")
    assert exc.value.reason == "invalid_structure"
    assert "recent_suppliers" in exc.value.field_errors


def test_unknown_field_is_rejected_not_dropped():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("schema_version: 1\nfoo: bar\n")
    assert exc.value.reason == "unknown_field"
    assert "foo" in exc.value.field_errors


def test_top_level_must_be_mapping():
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences("- a\n- b\n")
    assert exc.value.reason == "invalid_structure"


def test_list_length_limit_is_rejected():
    values = "\n".join(f"  - s{i}" for i in range(11))
    with pytest.raises(PreferenceSchemaError) as exc:
        load_preferences(f"schema_version: 1\nrecent_suppliers:\n{values}\n")
    assert "recent_suppliers" in exc.value.field_errors


def test_merge_preserves_explicit_fields_and_dedupes():
    prefs = load_preferences(VALID_YAML).preferences
    merged = merge_auto_fields(prefs, ["博世", "新供应商", "新供应商"], "新的比价")
    assert merged.preferred_output == "table"
    assert merged.preferred_chart_type == "bar"
    assert merged.preferred_currency == "CNY"
    assert merged.preferred_language == "zh"
    assert merged.recent_suppliers == ["博世", "新供应商", "大陆"]
    assert merged.recent_queries == ["新的比价", "刹车片比价"]


def test_merge_respects_limits_and_keeps_newest():
    prefs = UserPreferences(
        recent_suppliers=[f"s{i}" for i in range(10)],
        recent_queries=[f"q{i}" for i in range(5)],
    )
    merged = merge_auto_fields(prefs, ["new"], "new-query")
    assert len(merged.recent_suppliers) == 10
    assert merged.recent_suppliers[0] == "new"
    assert len(merged.recent_queries) == 5
    assert merged.recent_queries[0] == "new-query"


def test_model_strictly_rejects_implicit_conversion():
    with pytest.raises(Exception):
        UserPreferences(preferred_output="table", schema_version="1")
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
