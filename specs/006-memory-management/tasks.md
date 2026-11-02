# 任务清单：长期记忆的查看、遗忘、过期与审计

- 对应规格：`specs/006-memory-management/spec.md`
- 对应方案：`specs/006-memory-management/plan.md`
- 状态：已完成（2026-11-02）

## 阶段一：schema v3 与过期

- [x] T1 RecentMemoryItem、schema v3、v0/v1/v2→v3 迁移
  - 文件：`src/agent/memory/preferences.py`
  - 测试：`tests/test_preference_schema.py`
- [x] T2 TTL/prune、同值刷新 last_seen、数量上限
  - 文件：`src/agent/memory/preferences.py`
- [x] T3 更新候选/中间件/服务测试中的 recent 条目断言
  - 文件：`tests/test_memory_middleware.py`、`tests/test_preference_service.py`

## 阶段二：服务与管理接口

- [x] T4 service：get_preferences/delete_recent_item/clear_all
  - 文件：`src/agent/memory/preference_service.py`
- [x] T5 memory API（GET/DELETE item/DELETE all）
  - 文件：`src/api_view/api/memory.py`、`src/api_view/web_main.py`
  - 测试：`tests/test_memory_api.py`
- [x] T6 审计与删除会话隔离测试
  - 文件：`tests/test_memory_api.py`、`tests/test_history_isolation.py`

## 阶段三：前端

- [x] T7 memory API client + MemoryPanel
  - 文件：`frontend/src/api/memory.js`、`components/MemoryPanel.vue`
- [x] T8 用户菜单/App 接线
  - 文件：`components/AppHeader.vue`、`App.vue`
  - 验证：`cd frontend && npm run build`

## 阶段四：验收

- [x] T9 文档/提示词 schema v3
  - 文件：`src/agent/memory/AGENTS.md`、`prompts.py`、`README.md`
- [x] T10 全量回归与 006 证据
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`

## 完成定义

- [x] AC-1~AC-9 有测试
- [x] 删除会话不影响偏好
- [x] 删除单条/清空/过期/审计可用
- [x] 前端构建通过
