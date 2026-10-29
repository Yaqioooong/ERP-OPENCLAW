# 规格：偏好记忆结构化与版本化

- 规格编号：`003`
- 状态：已确认（2026-10-29）
- 提出人：待填
- 日期：2026-10-29
- 前置规格：`specs/001-trusted-memory-identity/spec.md`

## 1. 背景与目标

`UserPreferences` 已存在，但自动记忆中间件仍通过逐行字符串拼接维护
`/memories/{user_id}/preferences.md`：

- 字段类型、枚举范围、缩进与旧数据兼容都依赖“格式碰巧正确”；
- 非法枚举或错误类型可能被当作字符串写回，造成偏好被静默破坏；
- 没有版本号，无法判断文件是旧格式还是新格式；
- 未知字段可能被丢弃或覆盖，缺少明确策略。

目标：用唯一的 Pydantic 模型定义偏好，读写都经过反序列化/校验/序列化；非法文件不得触发覆盖写入；旧版本按明确规则迁移；自动更新只改自动维护字段，显式偏好保持不变。

## 2. 已确认的决策

| 编号 | 决策 | 结论 |
| --- | --- | --- |
| D-1 | 当前 schema 版本 | `schema_version = 1` |
| D-2 | 旧版本识别 | 无 `schema_version` 或值为 `0` 视为 v0，读取时迁移为 v1 |
| D-3 | 未来版本 | `schema_version > 1` 视为不支持，读取失败，禁止写回 |
| D-4 | 未知字段策略 | **拒绝写入**：反序列化报 `extra_forbidden`，记录字段名告警，不覆盖原文件 |
| D-5 | 类型策略 | Pydantic `strict=True`；不做数字/字符串/枚举的隐式转换 |
| D-6 | 自动更新范围 | 只更新 `recent_suppliers` / `recent_queries`；其余字段原样保留 |
| D-7 | 文件格式 | YAML，UTF-8，`schema_version` 在第一行；使用 `yaml.safe_load/safe_dump`，不做手工行拼接 |

## 3. 唯一模型

```python
class UserPreferences(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: int = 1
    preferred_output: Literal["table", "chart"] | None = None
    preferred_chart_type: ChartType | None = None
    preferred_currency: Literal["CNY", "USD", "EUR", "JPY", "HKD", "GBP"] | None = None
    preferred_language: Literal["zh", "en"] | None = None
    recent_suppliers: list[str]   # 最多 10 条
    recent_queries: list[str]     # 最多 5 条
```

- `preferred_output` 只允许 `table` / `chart`。
- `preferred_chart_type` 只允许 `generate_visualization` 支持的 26 种图表类型（与 `CHART_TYPE_GUIDE` 对齐）。
- 字符串列表元素必须是非空字符串；超过数量上限视为非法，不在读取阶段静默截断。
- 自动更新阶段的合并、去重、截断是显式业务规则，不属于反序列化。

## 4. 读写流程

```text
读取文件
  → yaml.safe_load
  → 空文件：默认模型（v1）
  → 无版本/版本 0：注入 v1，记录 migrated=True
  → 版本 > 1：PreferenceSchemaError(unsupported_version)
  → Pydantic strict 校验
      ├─ 失败：记录 reason + field_errors（不记录正文），终止，不写回
      └─ 成功：UserPreferences
  → 仅更新 recent_suppliers / recent_queries
  → yaml.safe_dump(model_dump(mode="json"))
  → 写回
```

- 缩进错误、非法枚举、错误字段类型、未知字段、版本不支持 → `PreferenceSchemaError`，中间件跳过写入。
- 旧版本成功读取后，在首次自动更新或迁移写出时持久化为 v1；显式字段原样保留。
- 文件不存在：使用默认模型，只有产生新的自动数据时才创建文件。
- 日志只记录 `user_id/request_id/thread_id/reason/field_names`，不记录偏好正文。

## 5. 验收标准

### AC-1 合法文件稳定读取

- Given 合法 v1 YAML，包含显式偏好与 recent 列表
- When 解析
- Then 得到强类型 `UserPreferences`，`preferred_output` 等枚举值保持原值，列表仍是 `list[str]`，`schema_version=1`。

### AC-2 非法结构被识别且不覆盖

- Given 缩进错误、`preferred_output: pie`、`recent_suppliers: "not-a-list"`、未知字段等文件
- When 自动记忆中间件尝试更新
- Then 返回解析错误，记录原因与字段名，**不调用写回**；原文件内容保持不变。

### AC-3 未知字段有明确策略

- Given 文件包含模型未定义的字段
- When 读取/自动更新
- Then 按 D-4 拒绝写入并告警；不得静默丢弃或原样复制。

### AC-4 旧版本迁移规则明确

- Given 无 `schema_version` 或 `schema_version: 0` 的旧文件
- When 读取
- Then 按 v0→v1 规则补默认值、保留显式字段并标记迁移；下一次成功更新写出 v1。
- Given `schema_version: 2`
- Then 报 `unsupported_version`，不写入。

### AC-5 自动更新不破坏显式偏好

- Given 合法文件 `preferred_output: table`、`preferred_chart_type: bar` 等
- When 自动抽取到新供应商/查询
- Then 只追加/去重 `recent_suppliers`、`recent_queries`，显式字段逐字段不变，`schema_version=1`。

### AC-6 序列化可回读

- Given 任意合法 `UserPreferences`
- When 序列化为 YAML 再解析
- Then 两者字段值一致，输出为可读 YAML，`schema_version` 位于文件开头。

### AC-7 日志不泄露正文

- Given 非法或合法更新
- When 检查日志
- Then 只包含用户/请求关联、错误原因和字段名，不包含偏好正文、供应商名、查询文本。

### AC-8 回归

- Given 003 实施完成
- When 执行全量测试
- Then 001/002 的记忆隔离、身份与权限测试仍通过。

## 6. 范围

**包含：**

- Pydantic `UserPreferences` 模型与枚举/数量/类型校验。
- YAML 反序列化、序列化、`schema_version` 与 v0→v1 迁移。
- 自动记忆中间件改为模型化读写；非法不写回。
- 未知字段拒绝策略与告警。
- 相关测试与 Agent 记忆格式文档更新。

**不包含（Out of Scope）：**

- 其他记忆文件（技能、AGENTS.md）的 schema 化。
- 偏好文件的加密、审计查询界面、跨用户迁移。
- 自动把既有线上 v0 文件批量重写；迁移为读取时惰性执行。

## 7. 约束

- 不新增运行时依赖：使用已安装的 `PyYAML` 与 `Pydantic`。
- 现有路径 `/memories/{user_id}/preferences.md`、命名空间和 `UserMemoryBackend` 保持不变。
- `preferred_chart_type` 的允许值与 `CHART_TYPE_GUIDE` 保持同步；新增图表类型时需提升 `schema_version` 或同步修改模型。
- 严格模式意味着旧文件中的 `preferred_output: Table` 不会被自动小写化，会报错；这是有意的安全边界。

## 8. 未决问题

| 编号 | 问题 | 结论 |
| --- | --- | --- |
| Q-1 | 未知字段保留还是拒绝？ | 拒绝写入（D-4），避免未校验数据被传播 |
| Q-2 | 旧文件是否立即批量迁移？ | 否，读取时惰性迁移，首次成功更新写出 v1 |
| Q-3 | 超出 recent 列表上限是否截断？ | 读取阶段拒绝；自动合并阶段按业务规则截断 |

## 9. 验收证据（2026-10-29）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 | `tests/test_preference_schema.py::test_valid_document_keeps_types_and_enums` |
| AC-2 | `tests/test_memory_middleware.py::test_invalid_preference_structures_never_write`（YAML/枚举/类型参数化），`tests/test_preference_schema.py::test_invalid_yaml_indentation_is_rejected` |
| AC-3 | `test_unknown_field_is_rejected_not_dropped`、`test_unknown_field_is_not_written_and_value_is_not_logged`：拒绝写入并告警字段名 |
| AC-4 | `test_legacy_document_without_version_is_migrated`、`test_legacy_zero_version_is_migrated`、`test_future_version_is_rejected`、`test_legacy_preferences_are_migrated_even_without_new_entities` |
| AC-5 | `test_auto_update_preserves_explicit_preferences`：显式枚举逐项不变，仅更新 recent 列表 |
| AC-6 | `test_dump_roundtrip_and_version_first` |
| AC-7 | `test_unknown_field_is_not_written_and_value_is_not_logged`、`test_middleware_does_not_log_content` |
| AC-8 | 全量回归：`221 passed`，001/002 测试仍通过 |

模型为 `src/agent/memory/preferences.py` 中的唯一 `UserPreferences`；`src/agent/schema.py` 仅 re-export。

### 配置/格式

- 当前版本：`schema_version: 1`。
- 未知字段：拒绝写入，日志记录字段名。
- 旧版本：无版本或 `0` 惰性迁移；`>1` 不支持。

