# 规格：订单核验不可用与结果未知的状态语义

- 规格编号：`013`
- 状态：已实现（2026-11-05）
- 提出人：采购任务使用者
- 日期：2026-11-05
- 关联需求：ERP 订单查询 502/超时被计入“确认未创建”的尝试次数，且取消后页面把旧 UNKNOWN 错误显示成失败

## 1. 背景与目标

订单执行结果未知时，系统需要继续核验。当前把“查询订单服务不可用（502/超时/连接失败）”和“查询成功但暂未找到订单”都归为 `ORDER_RESULT_UNKNOWN`，并共用 `verification_attempts` 计数。

这会带来两个问题：

1. 502/超时没有任何“订单不存在”的证据，却会增加确认未创建的尝试次数，可能让系统过早进入 `ORDER_NOT_FOUND_CONFIRMED`；
2. 用户在结果未知时取消任务，任务被写成 `cancelled`，之后后台确认未创建也不能回写；页面继续显示旧的 502 错误，看起来像 `UNKNOWN` 直接失败。

本次目标：区分“核验不可用”和“结果未知”，把 502/超时从“未找到订单”的确认计数中剥离；用户在订单结果未知时停止任务，进入人工复核而不是普通取消。

## 2. 用户故事

- 作为采购任务发起人，我希望 ERP 订单查询 502/超时时页面明确说“核验服务暂不可用”，而不是让我确认“未创建”。
- 作为采购任务发起人，我希望服务恢复后可以继续核验，而不是因为几次 502 就被判定订单不存在。
- 作为采购任务发起人，当我在订单结果未知时取消，我希望系统提示订单可能已创建并进入人工复核，而不是静默结束。
- 作为系统维护者，我希望 502/超时和“查询成功但为空”使用不同计数和错误码，便于排查。

## 3. 业务规则

- `ORDER_VERIFY_UNAVAILABLE` 表示核验通道不可用（502/超时/连接失败），不是订单不存在。
- `ORDER_RESULT_UNKNOWN` 表示核验通道可用，但仍无法确定订单结果（例如暂未查到、等待确认窗口）。
- 502/超时只能增加 `verification_unavailable_attempts`，不得增加用于确认未创建的 `verification_attempts`。
- 只有“查询成功且订单为空”的核验才能增加未找到计数，并参与 `ORDER_NOT_FOUND_CONFIRMED` 的确认窗口。
- 核验不可用时任务保持 `awaiting_retry`，允许重试核验；不得因为 502 直接进入 `failed` 或 `not_created`。
- 用户在 `execution_status=unknown` 时取消任务，任务进入 `awaiting_review`，错误码 `ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL`，提示订单可能已创建；不得写成普通 `cancelled`。
- 非订单未知场景的取消仍进入 `cancelled`。
- `cancelled` 不是失败；任务面板不得把 `cancelled` 渲染成“任务未完成 + 旧错误”。
- 历史 `cancelled` 任务保留兼容；页面显示“任务已取消”，如执行结果未知则提示人工核对。
- 不改变审批、幂等键、订单创建和人工复核接口。

## 4. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 502 归类为核验不可用 | 订单查询返回 502 | 执行核验 | 执行记录为 `UNKNOWN`，错误码 `ORDER_VERIFY_UNAVAILABLE`；任务保持 `awaiting_retry` |
| AC-2 | 502 不计入未找到次数 | 已发生 1 次 502，确认窗口需要 2 次未找到 | 再执行 1 次查询且订单为空 | 仍未确认未创建；只有第 2 次成功空查询才进入 `ORDER_NOT_FOUND_CONFIRMED` |
| AC-3 | 502 计入独立计数 | 订单查询返回 502 | 核验完成 | `verification_unavailable_attempts` 增加 1；`verification_attempts` 不变 |
| AC-4 | 结果未知时取消 | 任务在 `awaiting_retry`，`execution_status=unknown` | 用户选择取消 | 任务进入 `awaiting_review`，错误码 `ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL`，`requires_manual_review=true` |
| AC-5 | 普通取消 | 任务在 `awaiting_retry`，但订单尚未执行或结果已确定 | 用户选择取消 | 任务仍进入 `cancelled` |
| AC-6 | 取消页面文案 | 任务为 `cancelled` | 打开任务面板 | 显示“任务已取消”，不再显示“任务未完成 + ORDER_RESULT_UNKNOWN” |
| AC-7 | 历史取消兼容 | 存量任务为 `cancelled` 且执行结果未知 | 打开任务面板 | 显示已取消提示，并提示订单结果未确认；不回写旧错误为失败 |
| AC-8 | 人工复核不变 | 任务为 `awaiting_review` | 打开任务面板 | 仍可确认已创建/未创建；人工确认后任务可完成或进入 `not_created` |
| AC-9 | 存量数据兼容 | 升级前执行记录没有新计数字段 | 升级后读取记录 | 新字段默认 0；旧记录仍可读取和核验 |
| AC-10 | 事件与日志 | 502 或取消未知任务 | 记录任务事件 | 事件状态与实际任务状态一致；不把 502 记为未创建确认 |

## 5. 范围

**包含：**

- 新增 `ORDER_VERIFY_UNAVAILABLE` 语义和独立计数。
- 订单对账在 502/超时时使用独立计数，不参与未找到确认。
- 未知结果取消进入人工复核。
- 任务面板区分 `cancelled` 与失败，并处理旧错误。
- 数据库兼容迁移、事件和测试。

**不包含：**

- 修改订单创建幂等键、审批或订单写入逻辑。
- 自动重试 ERP 或自动取消订单。
- 回填历史 `verification_attempts` 中可能混入的 502 次数。
- 新增外部对账系统或告警平台。

## 6. 约束与依赖

- 现有执行记录表 `verification_attempts` 继续表示“有效未找到/复核尝试”；新增字段表示核验不可用尝试。
- 新字段需兼容存量表：使用 `ADD COLUMN IF NOT EXISTS`，默认 0。
- `cancelled/rejected` 仍不允许普通对账覆盖；未知结果取消改为 `awaiting_review`，因此可被后续对账更新。
- 任务面板只做展示和入口控制，权限仍由后端任务接口保证。
- 既有 `not_created`、`awaiting_review` 人工复核能力不得回归。

## 7. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 502/超时是否允许自动重试？ | 影响用户体验与 ERP 压力 | 采购任务使用者 | 已确认：本期仍由用户重试核验，不新增自动重试 |
| Q-2 | 未知结果取消是否允许直接结束为 `cancelled`？ | 影响订单事实一致性 | 采购任务使用者 | 已确认：不允许，进入 `awaiting_review` |
| Q-3 | 历史取消任务是否回填？ | 影响历史数据 | 采购任务使用者 | 已确认：不回填，只修正展示 |
| Q-4 | 502 是否保留 `ORDER_RESULT_UNKNOWN` 兼容码？ | 影响旧前端/日志 | 采购任务使用者 | 已确认：新记录使用 `ORDER_VERIFY_UNAVAILABLE`；旧记录保持原样 |

## 8. 验收证据

2026-11-05 实施证据：

- AC-1、AC-3：`tests/test_order_reconciliation.py::test_gateway_unavailable_stays_unknown` 验证 502 后执行记录为 `UNKNOWN`、错误码 `ORDER_VERIFY_UNAVAILABLE`、`verification_attempts=0`、`verification_unavailable_attempts=1`。
- AC-2：`test_unavailable_does_not_advance_not_found_confirmation` 验证 1 次 502 后需要 2 次成功空查询才进入 `ORDER_NOT_FOUND_CONFIRMED`。
- AC-4、AC-5：`tests/test_procurement_recovery.py::test_persistent_unknown_enters_retry_without_recreating` 在重试后取消，验证任务进入 `awaiting_review`、`requires_manual_review=true`、错误码 `ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL`，且没有重复创建/核验。
- AC-6、AC-7：`frontend/src/components/ProcurementTaskPanel.vue` 的 `cancelledMessage` 只在 `cancelled + execution_status=unknown` 时提示订单结果未确认；`frontend/src/components/TaskCard.vue` 不再把 cancelled 的旧错误当失败摘要。
- AC-8：`tests/test_task_reconciliation.py` 人工复核与 `not_created` 测试保持通过；新增 `test_unavailable_does_not_change_task_or_emit` 验证核验不可用不改任务。
- AC-9：`procurement_order_execution` 新增 `verification_unavailable_attempts` 列，使用 `ADD COLUMN IF NOT EXISTS`，默认 0。
- AC-10：任务事件沿用现有 `task.progress/task.reconciled`，状态取自任务记录；没有新增把 502 记为未创建确认的逻辑。

命令与结果：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_order_reconciliation.py \
  tests/test_procurement_recovery.py \
  tests/test_task_reconciliation.py \
  -o addopts='' -q
# 37 passed
```

```bash
cd frontend && npm run build
# vite build 成功
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -o addopts='' -q \
  -k 'not test_resolve_part_by_id_rejects_conflicting_part_code'
# 415 passed, 1 deselected, 1 warning
```

数据库检查：

```text
procurement_order_execution.verification_unavailable_attempts
type=integer, default=0
```

说明：全量测试仍排除既有的 `test_resolve_part_by_id_rejects_conflicting_part_code`；该用例单独运行也失败，与本次状态语义修改无关。

