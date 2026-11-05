# 规格：确认未创建后的复核状态收口

- 规格编号：`012`
- 状态：已实现（2026-11-05）
- 提出人：采购任务使用者
- 日期：2026-11-05
- 关联需求：修复订单结果人工确认“未创建”后，任务面板仍停留在可点击“确认未创建”的问题

## 1. 背景与目标

订单结果 `unknown` 时，用户可以人工确认“未创建”，任务会被写成 `not_created`。该状态表示“已确认远端未创建订单，仍可人工回转”，不是待复核的 `awaiting_review`。

当前任务面板把 `not_created` 仍然算作待复核状态，因此确认成功后仍展示“确认未创建”按钮，用户会误以为操作没有生效。本次目标是把 `not_created` 收口为“已处理”展示，同时保留必要的纠错出口。

## 2. 用户故事

- 作为采购任务发起人，我确认“未创建”后，希望页面明确显示已处理，而不是继续让我确认同一个动作。
- 作为采购任务发起人，如果后续发现订单实际已创建，我希望仍能通过人工回转修正。
- 作为系统维护者，我希望任务状态同步失败时页面明确提示，而不是假装成功。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 确认未创建后收口 | 任务处于 `awaiting_review`，用户点击“确认未创建”且后端返回 `task_synced=true` | 页面刷新任务详情 | 任务显示 `not_created`；不再显示可点击的“确认未创建”按钮；显示“已确认未创建”状态说明 |
| AC-2 | 保留纠错出口 | 任务已为 `not_created`，且存在执行记录 | 用户展开纠错区域并填写订单号 | 可以执行“确认已创建”人工回转；成功后任务进入 `completed` |
| AC-3 | 待复核状态不变 | 任务仍为 `awaiting_review` 或 `requires_manual_review=true` 且不是 `not_created` | 打开任务面板 | 仍展示“确认未创建/确认已创建”复核按钮 |
| AC-4 | 同步失败提示 | 人工确认接口返回 `task_synced=false` 且任务仍未进入已处理状态 | 页面刷新后 | 显示同步失败提示，不展示成功 toast |
| AC-5 | 终态不误显示 | 任务为 `completed`、`failed`、`cancelled` 或 `rejected` | 打开任务面板 | 不显示待复核按钮 |
| AC-6 | 后端标记一致 | 任务状态为 `not_created` | 读取任务详情 | `requires_manual_review=false`，避免旧 checkpoint 标记把页面拉回复核态 |

## 4. 范围

**包含：**

- 任务面板对 `not_created` 的展示收口。
- 保留 `not_created` 下的人工回转入口。
- 人工确认接口 `task_synced` 的前端处理。
- 后端任务读取时对 `not_created` 清除 `requires_manual_review`。

**不包含：**

- 修改订单对账、幂等键、审批或任务状态机。
- 改变 `not_created` 的业务含义。
- 新增自动重试或自动回转。
- 修改任务来源对话绑定。

## 5. 约束与依赖

- `not_created` 是非终态，允许人工回转，不能简单删除该状态。
- 人工确认未创建后，执行记录状态为 `failed`，错误码为 `ORDER_NOT_FOUND_CONFIRMED`。
- 前端只做展示和入口控制，权限仍由后端任务接口保证。
- 既有 `awaiting_review` 复核流程不得回归。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | `not_created` 下是否保留“确认已创建”纠错入口？ | 影响人工回转能力 | 采购任务使用者 | 已确认：保留，放在折叠的纠错区域 |
| Q-2 | `task_synced=false` 是否允许仍提示成功？ | 影响用户对状态一致性的判断 | 采购任务使用者 | 已确认：不允许；任务未收口时提示同步失败 |

## 7. 验收证据

2026-11-05 实施证据：

- AC-1：`frontend/src/components/ProcurementTaskPanel.vue` 的 `requiresReview` 排除 `not_created`；`reviewResolved` 提供已处理区域，且不再渲染“确认未创建”按钮。
- AC-2：已处理区域保留折叠的“确认已创建”纠错入口，复用 `submitReview('mark_succeeded')`。
- AC-3：`requiresReview` 仍覆盖 `awaiting_review` 与 `requires_manual_review=true`。
- AC-4：`submitReview` 读取 `task_synced`；任务未进入 `completed/not_created/failed` 时提示同步失败，不再直接 toast 成功。
- AC-5：`completed/failed/cancelled/rejected` 不满足 `requiresReview`，不显示复核按钮。
- AC-6：`ProcurementTaskRunner._record_snapshot_result` 对 `NOT_CREATED` 强制 `requires_manual_review=false`；`tests/test_task_reconciliation.py::test_not_created_snapshot_clears_manual_review_flag` 通过。
- 前端源码检查：`tests/test_task_reconciliation.py::test_frontend_review_entry_is_wired` 校验 `reviewResolved`、`task_synced` 和 `not_created` 分支存在。

命令与结果：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_task_reconciliation.py -o addopts='' -q
# 14 passed
```

```bash
cd frontend && npm run build
# vite build 成功
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -o addopts='' -q \
  -k 'not test_resolve_part_by_id_rejects_conflicting_part_code'
# 413 passed, 1 deselected, 1 warning
```

说明：全量测试仍排除既有的 `test_resolve_part_by_id_rejects_conflicting_part_code`；该用例单独运行也失败，与本次界面状态修复无关。

