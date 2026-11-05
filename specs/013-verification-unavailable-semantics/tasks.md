# 任务清单：订单核验不可用与结果未知的状态语义

- 对应规格：`specs/013-verification-unavailable-semantics/spec.md`
- 对应方案：`specs/013-verification-unavailable-semantics/plan.md`
- 状态：已完成

## 任务

- [x] T1 模型与执行仓储新增核验不可用计数
  - 文件：`src/agent/procurement/models.py`、`src/agent/procurement/execution_repository.py`
  - 验证：新列自动创建；`ExecutionRecord` 可读取
- [x] T2 对账区分 502/超时与成功空查询
  - 文件：`src/agent/procurement/order_reconciliation.py`
  - 验证：502 不增加 `verification_attempts`；增加 `verification_unavailable_attempts`
- [x] T3 任务回写忽略 UNAVAILABLE
  - 文件：`src/agent/procurement/task_reconciliation.py`
  - 验证：核验不可用不把任务改成 not_created/awaiting_review
- [x] T4 未知结果取消进入人工复核
  - 文件：`src/agent/procurement/nodes.py`
  - 验证：取消后 `status=awaiting_review`，错误码 `ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL`
- [x] T5 前端区分取消与失败
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`、`frontend/src/components/TaskCard.vue`
  - 验证：cancelled 不再显示“任务未完成 + 502”；未知取消提示人工核对
- [x] T6 补测试
  - 文件：`tests/test_order_reconciliation.py`、`tests/test_procurement_recovery.py`、`tests/test_task_reconciliation.py`
  - 验证：37 passed
- [x] T7 前端构建与全量回归
  - 验证：`npm run build` 通过
  - 验证：415 passed, 1 deselected
- [x] T8 回填规格证据与 README
  - 文件：`specs/013-verification-unavailable-semantics/spec.md`、`README.md`

## 完成定义

- [x] 502 不增加未找到确认次数
- [x] 未知结果取消进入 `awaiting_review`
- [x] 页面不再把 cancelled 显示成旧 502 失败
- [x] 测试、迁移和构建通过
