# 规格：订单对账结果回写任务与人工复核

- 规格编号：`014`
- 状态：已确认（2026-11-05，按推荐默认值实施）
- 提出人：采购 Agent 使用者
- 日期：2026-11-05
- 关联需求：失败降级专项 P0-5；`specs/009-single-order-write-path`

## 1. 背景与目标

当前订单对账只更新 `procurement_order_execution`，不更新采购任务：

- 订单实际已创建、后台对账确认成功，任务仍停在 `awaiting_retry` 或 `failed`；
- 订单内容不一致（`needs_review`）后没有产品出口，用户只能看到失败；
- “确认未创建”直接写失败终态，且退出对账队列；若后续发现订单存在，无法回转；
- 用户可能因为任务显示失败而重新发起，造成重复下单。

本次目标：把对账结果回写到任务状态与事件；提供任务级人工复核出口；
“确认未创建”改为非终态、可人工回转；不改变订单写入唯一路径。

## 2. 用户故事

- 作为采购任务发起人，我希望后台对账确认订单已创建后，任务自动变为已完成并显示订单号，
  而不是一直显示“待恢复”。
- 作为审批人/管理员，我希望订单内容不一致时，任务明确进入“待人工复核”，
  并能查看原因、确认已创建或确认未创建。
- 作为采购任务发起人，我希望“确认未创建”不是永久失败，后续查到订单时仍可人工标记成功。
- 作为排障人员，我希望对账回写有事件和日志，能看到任务从哪个状态变成哪个状态。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 对账成功回写 | 任务处于 `awaiting_retry`/`failed`/`verifying`，执行记录被对账确认成功 | worker 对账 | 任务变为 `completed`，写入 `order_id`，清空错误，`requires_manual_review=false`；发出 `task.reconciled` 事件 |
| AC-2 | 内容不一致 | 远端订单存在但与方案不一致 | worker 对账 | 任务变为 `awaiting_review`，`requires_manual_review=true`，错误码 `ORDER_CONTENT_MISMATCH`；发出事件 |
| AC-3 | 远端取消 | 远端订单状态为已取消 | worker 对账 | 任务变为 `awaiting_review`，错误码 `ORDER_CANCELLED_REMOTE`；发出事件 |
| AC-4 | 确认未创建 | 确认窗口内远端仍无订单 | worker 对账 | 任务变为 `not_created`（非终态），错误码 `ORDER_NOT_FOUND_CONFIRMED`；不得直接 `failed` |
| AC-5 | 未确定 | 对账仍 UNKNOWN | worker 对账 | 任务状态不变；不误报成功/失败 |
| AC-6 | 人工确认已创建 | 执行记录 `needs_review`/`not_created`，用户提供 order_id | 调用 resolve mark_succeeded | 执行记录成功，任务变为 `completed`，显示订单号；发出事件 |
| AC-7 | 人工确认未创建 | 执行记录需复核 | 调用 resolve mark_failed | 执行记录失败，任务变为 `not_created`（非终态），可后续回转 |
| AC-8 | 任务查询一致性 | 任务已由对账回写 | `GET /tasks/{id}` | 返回回写后的状态、order_id、requires_manual_review、execution_key；不得回退为旧 checkpoint 的 `failed` |
| AC-9 | 启动对账不覆盖 | 任务已是 `awaiting_review`/`not_created` | 服务启动 reconcile | 不得用旧 checkpoint 的 `failed` 覆盖人工复核状态 |
| AC-10 | 事件驱动前端 | 对账回写 | 前端任务面板 | 收到 `task.reconciled` 后刷新任务，展示新状态/订单号/复核入口 |
| AC-11 | 复核入口 | 任务 `awaiting_review`/`not_created` 或 `requires_manual_review=true` | 打开任务面板 | 展示复核原因、execution_key；提供“确认已创建（填 order_id）”“确认未创建”操作 |
| AC-12 | 权限 | 非任务所有者且非 approver/admin | 调用复核接口 | 403，任务/执行记录不变 |
| AC-13 | 不重复下单 | 对账回写成功 | 用户刷新/重试 | 不得重新调用 create_order；任务终态 completed 幂等 |
| AC-14 | 幂等 | 同一对账结果重复回写 | worker/人工重复 | 状态和事件不重复；重复调用返回当前状态 |

## 4. 范围

**包含：**

- 任务状态 `awaiting_review`、`not_created`。
- 任务记录新增 `order_id`、`requires_manual_review`、`execution_key`、`reconciled_at`。
- 对账 worker 回写任务 + `task.reconciled` 事件。
- 人工 resolve 后回写任务。
- `GET /tasks/{id}` 读取回写状态。
- 任务面板复核区、API resolve、事件刷新。
- 数据库迁移（ADD COLUMN IF NOT EXISTS）。
- 单测/接口测试。

**不包含：**

- 自动重新执行 create_order（`not_created` 只提供人工复核/重新发起入口）。
- 修改订单内容、自动补偿、自动退款。
- 独立的管理员复核中心（先做任务面板内的任务级复核）。
- 修改订单创建唯一路径、审批规则或幂等键。

## 5. 约束与依赖

- 订单写入仍只走 `start_procurement_task`；复核只更新执行记录与任务读模型，不创建订单。
- 任务归属与角色仍由服务端认证/授权决定；approver/admin 可看/可复核。
- 对账回写必须允许从 `failed` 纠正，但不能覆盖 `cancelled/rejected`。
- 任务状态回写必须幂等；重复事件不重复发。
- 前端只做入口显隐，不作为权限边界。
- 现有执行记录状态不改枚举，`not_created` 用任务状态 + 执行记录错误码表达。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | `not_created` 是否终态？ | 能否回转 | 需求方 | 已确认：非终态，保留人工复核 |
| Q-2 | 复核角色？ | 权限边界 | 需求方 | 已确认：任务所有者或 approver/admin |
| Q-3 | 是否自动重跑 create_order？ | 重复下单风险 | 需求方 | 已确认：不自动重跑；`not_created` 提供重新发起入口 |
| Q-4 | 事件名？ | 前端刷新 | 需求方 | 已确认：`task.reconciled` |
| Q-5 | 复核面板位置？ | 产品入口 | 需求方 | 已确认：任务面板内，任务级复核 |

## 7. 验收证据

2026-11-05 实施证据：

- 单测（13 passed）：`tests/test_task_reconciliation.py`
  - SUCCEEDED → 任务 `completed` + order_id + 清错误 + `task.reconciled` 事件；
  - MISMATCH → `awaiting_review` + `requires_manual_review=true` + `ORDER_CONTENT_MISMATCH`；
  - CANCELLED → `awaiting_review` + `ORDER_CANCELLED_REMOTE`；
  - NOT_CREATED → `not_created`（非终态）+ `ORDER_NOT_FOUND_CONFIRMED`；
  - UNKNOWN 不改任务、不发事件；
  - 重复结果幂等（只发一次事件）；
  - 人工 mark_succeeded/mark_failed 后任务映射正确；
  - snapshot 优先任务记录，不被旧 checkpoint 的 failed 覆盖；
  - 启动 reconcile 跳过 `awaiting_review`/`not_created`；
  - completed 不会被迟到的 not_created 对账降级；
  - 复核权限：owner/approver/admin 可操作，其他 requester 不可；
  - 前端复核区/`task.reconciled`/新状态已接线。
- 数据库 roundtrip（真实测试库）：`PostgresTaskRepository.create` 修复参数错位；
  `apply_reconciliation` 可从 `awaiting_retry` → `completed` → `not_created`，
  `order_id/execution_key/reconciled_at/completed_at` 正确。
- 回归（136 passed）：`tests/test_order_reconciliation.py`、`tests/test_task_repository.py`、
  `tests/test_procurement_recovery.py`、`tests/test_procurement_workflow.py`、
  `tests/test_task_events.py`、`tests/test_task_event_sse.py`、`tests/test_health.py`、
  `tests/test_chat_identity.py`、`tests/test_procurement_task_tools.py`、
  `tests/test_agent_boundaries.py`、`tests/test_model_resilience.py`、
  `tests/test_tool_resilience.py`、`tests/test_delegation_contract.py` 等。
- 前端：`npm run build` 通过；任务面板新增复核区，`task.reconciled` 触发刷新。

已知限制：

- `not_created` 提供人工复核/返回对话重新发起，不自动重跑 `create_order`；
- 没有独立管理员复核中心，复核入口在任务面板内；
- 旧任务若缺少 `execution_key`，前端通过 `/executions/unknown` 回退查找；
- 对账 worker 仍按单进程定时任务运行，多实例 leader election 属于后续规格；
- `ORDER_EXECUTION_FAILED` 等业务失败不进入人工复核，保持原任务失败路径。
