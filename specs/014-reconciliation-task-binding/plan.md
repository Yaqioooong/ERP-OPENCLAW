# 计划：订单对账结果回写任务与人工复核（014）

- 规格：`specs/014-reconciliation-task-binding/spec.md`（已确认 2026-11-05）
- 目标：对账结果驱动任务状态与事件；人工复核有产品出口；`not_created` 非终态。

## 1. 方案

### 1.1 任务状态与字段

`TaskStatus` 新增：

- `awaiting_review`：订单内容不一致/远端取消，需人工复核；
- `not_created`：确认远端未创建，非终态，可人工回转。

`procurement_task` 新增列（`ADD COLUMN IF NOT EXISTS`）：

- `order_id TEXT`
- `requires_manual_review BOOLEAN NOT NULL DEFAULT FALSE`
- `execution_key TEXT`
- `reconciled_at TIMESTAMPTZ`

`TaskRecord` 同步字段。`TaskRunResult.state_summary` 增加 `execution_key`、
`order_id`、`requires_manual_review`。

`TaskRepository.apply_reconciliation(...)`：

- 幂等更新任务状态/订单号/复核标记/最近错误；
- 允许从 `failed`/`awaiting_retry`/`verifying`/`executing`/`approved_pending_execution`/
  `awaiting_review`/`not_created` 回写；
- 不覆盖 `cancelled/rejected`；
- 返回是否实际更新，供事件去重。

### 1.2 对账回调

新增 `src/agent/procurement/task_reconciliation.py`：

- `TaskReconciliationService.apply_verification(record, result)`：
  - SUCCEEDED → completed + order_id + 清错误
  - MISMATCH → awaiting_review + requires_manual_review + `ORDER_CONTENT_MISMATCH`
  - CANCELLED → awaiting_review + `ORDER_CANCELLED_REMOTE`
  - NOT_CREATED → not_created + `ORDER_NOT_FOUND_CONFIRMED`
  - UNKNOWN → no-op
- `apply_execution_record(record)`：人工 resolve 后按执行记录状态回写任务；
- 更新成功才发 `task.reconciled` 事件，data 只含 task_id/status/order_id/
  requires_manual_review/code/execution_key。

`OrderReconciler` 增加可选 `set_task_service(service)`；`reconcile_pending`
在 `verify_record` 后调用；失败只记日志，不影响对账主循环。

### 1.3 任务读取

`ProcurementTaskRunner`：

- `_snapshot`/`_terminal_snapshot_result` 优先使用任务记录的
  `order_id/requires_manual_review/execution_key`，避免旧 checkpoint 把
  `completed/awaiting_review/not_created` 回退；
- 启动 `reconcile()` 跳过 `awaiting_review/not_created`，不覆盖人工复核状态；
- `list_recoverable` 包含新状态，但 reconcile 显式跳过。

### 1.4 人工复核 API

`GET /api/procurement/executions/unknown` 扩展为“需要人工复核”的列表：

- 原有 `unknown/executing`；
- 新增 `needs_review`；
- 新增 `failed + ORDER_NOT_FOUND_CONFIRMED`（确认未创建）；
- 返回 `review_reason`、`task_status`、`requires_manual_review`。

`POST /api/procurement/executions/{key}/resolve` 保留 action：

- `mark_succeeded`（必须 order_id）
- `mark_failed`（确认未创建）

resolve 后调用 `TaskReconciliationService.apply_execution_record`，把结果回写任务并发事件。

### 1.5 前端

`frontend/src/components/ProcurementTaskPanel.vue`：

- 当任务 `awaiting_review`/`not_created` 或 `state_summary.requires_manual_review` 时，
  展示“订单结果复核”区；
- 显示 execution_key、复核原因、当前 order_id；
- “确认已创建”输入 order_id 后调用 resolve；
- “确认未创建”二次确认后调用 resolve；
- `not_created` 提供“返回对话重新发起”出口；
- 新状态加入状态标签/颜色/步骤状态。

`frontend/src/api/procurement.js` 增加 `resolveExecution`；
`useTaskStore` 在 `task.reconciled` 时刷新；`taskEvents` 加入事件类型。
`TaskCard.vue` 同步状态标签。

### 1.6 数据库兼容

`procurement_task` 建表语句包含新列，并对旧表执行
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`。`TaskRecord` 字段都有默认值，
旧行可直接读取。

## 2. 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/agent/procurement/models.py` | TaskStatus/TaskRecord 新字段 |
| `src/agent/procurement/task_repository.py` | 建表/迁移、create 参数修复、`apply_reconciliation`、recoverable 状态 |
| `src/agent/procurement/task_runner.py` | snapshot 覆盖、reconcile 跳过、state_summary |
| `src/agent/procurement/order_reconciliation.py` | task service 注入、worker 回写、manual review 列表 |
| `src/agent/procurement/task_reconciliation.py`（新增） | 状态映射与事件 |
| `src/agent/procurement/execution_repository.py` | `list_manual_review` |
| `src/agent/procurement/dependencies.py` | 构造/暴露 task reconciliation service |
| `src/agent/procurement/task_events.py` | `task.reconciled` 事件 |
| `src/api_view/api/procurement.py` | 复核列表/resolve 回写 |
| `frontend/src/api/procurement.js` | resolve API |
| `frontend/src/components/ProcurementTaskPanel.vue` | 复核区/状态 |
| `frontend/src/components/TaskCard.vue` | 新状态标签 |
| `frontend/src/composables/useTaskStore.js` | `task.reconciled` 刷新 |
| `frontend/src/api/taskEvents.js` | 事件类型 |
| `tests/test_task_reconciliation.py`（新增） | 状态映射/幂等/读取覆盖/启动 reconcile |
| `tests/test_health.py` 等既有测试 | 回归 |

## 3. 风险

| 风险 | 处理 |
| --- | --- |
| 旧 checkpoint 覆盖新状态 | snapshot 优先读任务记录；reconcile 跳过复核状态 |
| 对账回写覆盖取消/拒绝 | SQL WHERE 排除 cancelled/rejected |
| 重复事件 | apply 返回实际更新才 emit；重复状态不更新 |
| 人工 mark_failed 误判 | 明确二次确认；not_created 非终态，可再 mark_succeeded |
| 前端拿不到 execution_key | 新任务写入 state_summary；旧任务可通过扩展列表接口按 task_id 查找 |
| 生产旧表缺列 | `ADD COLUMN IF NOT EXISTS` |
| 任务创建 SQL 参数错位 | 修复 create 的多余参数（当前 14 个值对 13 个占位符） |

## 4. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1～AC-5 | `TaskReconciliationService` 单测：各 outcome 映射、UNKNOWN 不变 |
| AC-6/AC-7 | 人工 resolve 后 apply_execution_record 单测 |
| AC-8/AC-9 | runner snapshot/reconcile 使用 fake repo 验证不被旧 checkpoint 覆盖 |
| AC-10 | 事件类型/前端事件列表静态断言 |
| AC-11 | 前端源码断言 + API 测试 |
| AC-12 | 复核接口权限测试（owner/approver/admin/其他） |
| AC-13/AC-14 | 重复 apply 幂等；completed 不重新 create |
| 迁移 | `list_manual_review`/`apply_reconciliation` DB 测试（有测试库时） |

## 5. 实施顺序

1. 模型/仓储/SQL
2. task reconciliation service + worker 回写
3. runner 读取/启动对账保护
4. API + 事件
5. 前端复核区
6. 测试与文档
