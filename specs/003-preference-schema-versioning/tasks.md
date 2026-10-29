# 任务清单：偏好记忆结构化与版本化

- 对应规格：`specs/003-preference-schema-versioning/spec.md`
- 对应方案：`specs/003-preference-schema-versioning/plan.md`
- 状态：已完成（2026-10-29）

## 阶段一：模型与解析

- [x] T1 新增 Pydantic 偏好模型
  - 文件：`src/agent/memory/preferences.py`
  - 内容：`UserPreferences`、枚举字面量、strict/extra=forbid、列表上限
- [x] T2 YAML 读取、校验、v0→v1 迁移与序列化
  - 文件：`src/agent/memory/preferences.py`
  - 内容：`load_preferences`、`dump_preferences`、`PreferenceSchemaError`、版本规则
- [x] T3 schema re-export 与文档默认格式
  - 文件：`src/agent/schema.py`、`src/agent/memory/AGENTS.md`、`src/agent/memory/prompts.py`
- [x] T4 模型单元测试
  - 文件：`tests/test_preference_schema.py`
  - 覆盖：合法、非法 YAML/枚举/类型/未知字段、迁移、未来版本、roundtrip

## 阶段二：中间件切换

- [x] T5 中间件改用模型化读写
  - 文件：`src/agent/middlewares/memory_update.py`
  - 内容：删除手工行解析；只更新 recent 字段；非法不写回；迁移写回
- [x] T6 中间件测试
  - 文件：`tests/test_memory_middleware.py`
  - 覆盖：显式字段保持、非法结构零写入、旧文件迁移、去重/上限
- [x] T7 日志与审计约束
  - 文件：`src/agent/middlewares/memory_update.py`
  - 内容：错误只记录原因/字段名/关联 ID，不含正文

## 阶段三：验收

- [x] T8 全量回归与证据
  - 文件：`specs/003-preference-schema-versioning/spec.md`
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`

## 完成定义

- [x] AC-1~AC-8 有对应测试
- [x] 非法文件不触发写入
- [x] 自动更新不改变显式字段
- [x] 全量测试通过
