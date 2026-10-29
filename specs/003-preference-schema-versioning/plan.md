# 技术方案：偏好记忆结构化与版本化

- 对应规格：`specs/003-preference-schema-versioning/spec.md`
- 状态：已实现（2026-10-29）

## 1. 模型与模块

新增 `src/agent/memory/preferences.py`，作为偏好 schema 的唯一事实源：

| 内容 | 说明 |
| --- | --- |
| `CURRENT_SCHEMA_VERSION = 1` | 当前版本 |
| `UserPreferences` | Pydantic v2 模型，`strict=True, extra="forbid"` |
| `ChartType` | `Literal`，与 `CHART_TYPE_GUIDE` 的 26 种类型一致 |
| `PreferenceSchemaError` | 带 `reason` 与 `field_errors` 的领域异常 |
| `load_preferences(text)` | 返回 `(UserPreferences, migrated)` |
| `dump_preferences(prefs)` | 稳定 YAML 输出 |
| `merge_auto_fields(prefs, suppliers, query)` | 只合并自动字段，显式字段不变 |
| `default_preferences()` | v1 默认模型 |

`src/agent/schema.py` 的 `UserPreferences` 改为从该模块 re-export，保持既有导入路径。

### 1.1 校验

- `schema_version`：整数，v0 注入 1；>1 抛 `unsupported_version`；负数/非整数抛 `invalid_schema_version`。
- 枚举字段使用 `Literal`，严格模式接受 YAML 字符串、拒绝大小写/未知值。
- 列表使用 `Field(max_length=10/5)`；元素必须是非空字符串，不自动 trim 或去重。
- `extra="forbid"`：未知字段抛 `extra_forbidden`。
- YAML 解析错误、顶层非 mapping 分别抛 `yaml_error` / `invalid_structure`。

### 1.2 迁移

```text
无 schema_version 或 0 → 注入 1，保留已知显式字段，缺省字段用模型默认值，migrated=True
schema_version == 1     → 直接校验
schema_version > 1      → unsupported_version，拒绝
其他（负数/浮点/字符串/bool） → invalid_schema_version
```

未知字段在 v0 中同样拒绝（D-4），避免旧文件携带未校验字段进入新格式。

## 2. 自动更新中间件

`MemoryUpdateMiddleware` 的 `_merge_preferences` 行解析逻辑删除，改为：

1. 读取文件；不存在 → `default_preferences()`。
2. `load_preferences(text)`；失败 → 记录原因+字段名，`return None`，不写。
3. 只更新 `recent_suppliers` / `recent_queries`（新值优先、去重、上限 10/5）。
4. 显式字段从原模型复制，`schema_version` 固定为当前版本。
5. 若没有新数据且未发生迁移 → 不写；迁移旧文件或产生新数据 → `dump_preferences` 后写回。
6. 写回仍走 `CompositeBackend → UserMemoryBackend`，保留 001 的身份/审计约束。

日志：

```text
MemoryUpdateMiddleware: invalid preferences user=... reason=enum_error
  fields=preferred_output request_id=... thread_id=...
```

不记录 YAML 内容或字段值。

## 3. 文件清单

| 文件 | 改动 |
| --- | --- |
| `src/agent/memory/preferences.py` | 新增模型、解析、迁移、序列化 |
| `src/agent/schema.py` | `UserPreferences` 改为 re-export |
| `src/agent/middlewares/memory_update.py` | 删除行解析，改用模型化读写 |
| `src/agent/memory/AGENTS.md` | 默认 YAML 增加 `schema_version: 1`，标明允许值 |
| `src/agent/memory/prompts.py` | 新建偏好文件的默认字段加 `schema_version` |
| `tests/test_preference_schema.py` | 新增解析/迁移/序列化测试 |
| `tests/test_memory_middleware.py` | 增加非法不写回、显式字段保持测试 |

## 4. 测试策略

| 场景 | 测试 |
| --- | --- |
| 合法 v1 | 解析字段类型、枚举、列表；dump→load 一致 |
| 非法 YAML/缩进 | `yaml_error` |
| 非法枚举 | `preferred_output: pie` → `literal_error`，字段名可定位 |
| 错误类型 | `recent_suppliers: "x"`、`schema_version: "1"` → validation error |
| 未知字段 | `foo: bar` → `extra_forbidden` |
| 旧版本 | 无版本/0 → v1，显式字段保留，`migrated=True` |
| 未来版本 | version 2 → `unsupported_version` |
| 自动更新 | `preferred_output: table` 保持不变；recent 去重/上限 |
| 非法不覆盖 | Fake backend 断言 `awrite` 未被调用 |
| 迁移写回 | v0 文件在无新数据时也写出 v1 |
| 日志 | 不含供应商名/查询正文 |

## 5. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 严格枚举导致旧文件更新被阻塞 | 按 D-4 拒绝写入并告警；不破坏原文件，用户可手工修正 |
| 图表类型清单与工具漂移 | 在模型注释中标注同步要求；测试固定集合 |
| v0 未知字段导致迁移失败 | 明确拒绝；日志给出字段名，不使用默认值覆盖 |
| YAML 序列化格式变化 | 测试只断言解析后的语义与关键顺序，不依赖空行/引号细节 |
| 旧文件被自动重写 | 仅在合法读取且迁移/有新数据时写；写入前仍经过身份/审计 Backend |

回滚：删除 `preferences.py` 并恢复中间件行解析即可；偏好文件是数据，不回滚、不删除。

## 6. 实施顺序

1. 模型、解析、迁移、序列化 + 单元测试。
2. 中间件切换 + 非法不写回/显式字段保持测试。
3. 更新 Agent 记忆格式文档。
4. 全量回归与证据。


## 7. 实施结果（2026-10-29）

- 新增 `src/agent/memory/preferences.py`：Pydantic `UserPreferences`、严格枚举/类型、`schema_version`、v0→v1 惰性迁移、YAML 读写、自动字段合并。
- `src/agent/schema.py` 改为 re-export，保持既有导入路径；`MemoryUpdateMiddleware` 删除手工按行解析，非法文件不写回，显式偏好保持不变。
- `src/agent/memory/AGENTS.md` 与 `prompts.py` 的默认偏好增加 `schema_version: 1` 和允许值说明。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → **221 passed**。
- 与方案偏差：无；自动合并对超长查询/供应商名做显式截断（500 字符）以避免校验异常携带正文进入日志。
