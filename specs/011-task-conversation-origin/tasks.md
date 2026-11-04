# 任务清单：采购任务来源对话绑定

- 对应规格：`specs/011-task-conversation-origin/spec.md`
- 对应方案：`specs/011-task-conversation-origin/plan.md`
- 状态：已完成

> 规则：一条任务一次提交能完成；先契约/测试，后实现；每完成一条就勾选并记录验证结果。
> 发现规格问题先改 `spec.md`，不要用代码“顺手”改变行为。

## 阶段一：契约与测试

- [x] T1 补任务来源契约测试
  - 文件：`tests/test_procurement_task_tools.py`、`tests/test_procurement_recovery.py`
  - 覆盖：AC-1、AC-2、AC-5、AC-6、AC-10
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_procurement_task_tools.py tests/test_procurement_recovery.py -q`（通过）

- [x] T2 补仓储与 API 测试
  - 文件：`tests/test_task_repository.py`、`tests/test_procurement_auth.py`
  - 覆盖：AC-3、AC-4、AC-7、AC-8、AC-9、AC-12
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_task_repository.py tests/test_procurement_auth.py -q`（通过）

- [x] T3 补会话删除归属释放测试
  - 文件：`tests/test_thread_ownership.py`、`tests/test_history_isolation.py`
  - 覆盖：AC-11、AC-14
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_thread_ownership.py tests/test_history_isolation.py -q`（通过）

## 阶段二：后端实现

- [x] T4 模型与仓储支持来源
  - 文件：`src/agent/procurement/models.py`、`src/agent/procurement/task_repository.py`
  - 改动：`TaskRunResult.conversation_id`；`list_by_conversation`；来源索引
  - 验证：仓储测试通过

- [x] T5 Runner 透传来源并支持按对话查询
  - 文件：`src/agent/procurement/task_runner.py`
  - 改动：所有任务结果携带来源；新增 `list_by_conversation`
  - 验证：`tests/test_procurement_recovery.py` 通过

- [x] T6 聊天工具捕获来源并在缺失时拒绝创建
  - 文件：`src/agent/tools/procurement_task_tools.py`
  - 改动：从 `runtime.context.thread_id` 取来源；结果返回 `conversation_id`
  - 验证：`tests/test_procurement_task_tools.py` 通过

- [x] T7 API 校验来源、返回可用性、支持按对话查询
  - 文件：`src/api_view/api/procurement.py`
  - 改动：来源归属校验；`conversation_available`；`GET /api/procurement/tasks?conversation_id=`
  - 验证：`tests/test_procurement_auth.py` 通过

- [x] T8 删除会话释放归属 key
  - 文件：`src/api_view/thread_ownership.py`、`src/api_view/api/history.py`
  - 改动：新增 `release`；删除会话成功后释放归属
  - 验证：`tests/test_thread_ownership.py tests/test_history_isolation.py` 通过

## 阶段三：前端集成

- [x] T9 任务面板展示来源并支持返回来源对话
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`、`frontend/src/App.vue`、`frontend/src/api/procurement.js`
  - 改动：来源行、返回入口、可选来源创建、按对话查询 API
  - 验证：`cd frontend && npm run build`（通过）

## 阶段四：验收

- [x] T10 逐条核对 `spec.md` 验收标准，回填验收证据
  - 文件：`specs/011-task-conversation-origin/spec.md`
  - 验证：每条 AC 有测试或可复现记录

- [x] T11 采购专项与全量回归
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_procurement_task_tools.py tests/test_procurement_recovery.py tests/test_procurement_auth.py tests/test_task_repository.py tests/test_thread_ownership.py tests/test_history_isolation.py -o addopts='' -q`（50 passed）
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/ -o addopts='' -q -k 'not test_resolve_part_by_id_rejects_conflicting_part_code'`（356 passed, 1 deselected）

- [x] T12 更新 README 与计划状态
  - 文件：`README.md`、`specs/011-task-conversation-origin/tasks.md`
  - 验证：文档与接口契约一致

## 完成定义（DoD）

- [x] 所有验收标准有对应测试或可复现的验证记录
- [x] 全量测试通过，无新增告警（除既有 `data_provider` 失败）
- [x] 接口契约与文档一致
- [x] 回滚方式明确且验证过
- [ ] 提交信息包含规格编号（待提交）
