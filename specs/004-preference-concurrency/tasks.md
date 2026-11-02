# 任务清单：偏好记忆并发安全与可判定写入结果

- 对应规格：`specs/004-preference-concurrency/spec.md`
- 对应方案：`specs/004-preference-concurrency/plan.md`
- 状态：已完成（2026-10-29）

## 阶段一：版本与锁

- [x] T1 preferences schema v2：`revision`、v0/v1→v2 迁移、dump/load
  - 文件：`src/agent/memory/preferences.py`
  - 测试：`tests/test_preference_schema.py`
- [x] T2 Redis 锁组件
  - 文件：`src/agent/memory/preference_lock.py`
  - 测试：`tests/test_preference_lock.py`（NX、超时、token 释放、Redis 异常）
- [x] T3 提交服务
  - 文件：`src/agent/memory/preference_service.py`
  - 内容：锁、revision、读失败关闭、冲突重读重合并、结构化结果
  - 测试：`tests/test_preference_service.py`

## 阶段二：写入边界

- [x] T4 Backend 阻断直写并提供内部写方法
  - 文件：`src/agent/memory/user_memory_backend.py`
  - 测试：`tests/test_memory_backend.py`
- [x] T5 显式偏好工具
  - 文件：`src/agent/tools/preference_tools.py`
  - 测试：`tests/test_preference_tool.py`
- [x] T6 中间件与主 Agent 装配
  - 文件：`src/agent/middlewares/memory_update.py`、`src/agent/main_agent.py`
  - 内容：只调用 service；失败不误报；工具加入主 Agent
- [x] T7 提示词与记忆规范
  - 文件：`src/agent/memory/AGENTS.md`、`src/agent/memory/prompts.py`
  - 内容：显式偏好必须用专用工具；schema v2/revision

## 阶段三：验收

- [x] T8 并发/异常/迁移测试补齐与全量回归
  - 覆盖：AC-1~AC-9
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`
- [x] T9 回填 004 验收证据与实现结果

## 完成定义

- [x] 并发不同变更均保留
- [x] 读异常零写入；写异常不误报成功
- [x] 显式保存返回 saved=true/false
- [x] 通用偏好直写被阻断
- [x] 全量测试通过
