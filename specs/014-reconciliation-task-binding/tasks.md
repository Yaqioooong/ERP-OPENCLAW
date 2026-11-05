# 任务：订单对账结果回写任务与人工复核（014）

- 规格：`specs/014-reconciliation-task-binding/spec.md`
- 计划：`specs/014-reconciliation-task-binding/plan.md`

## 任务清单

- [x] 1. `models.py` 增加 `awaiting_review`/`not_created` 和 TaskRecord 字段。验证：模型单测。
- [x] 2. `task_repository.py` 建表/迁移、修复 create 参数、`apply_reconciliation`、recoverable 状态。验证：仓储测试/静态断言。
- [x] 3. 新增 `task_reconciliation.py`：outcome → 任务状态映射、事件、幂等。验证：单测。
- [x] 4. `order_reconciliation.py` 注入 task service，worker 对账回写；新增人工复核列表。验证：单测。
- [x] 5. `execution_repository.py` 增加 `list_manual_review`。验证：DB/静态。
- [x] 6. `task_runner.py` snapshot 优先任务记录、启动 reconcile 跳过复核状态。验证：单测。
- [x] 7. `dependencies.py` 构造并暴露 task reconciliation service。验证：静态/单测。
- [x] 8. `task_events.py` 增加 `task.reconciled`。验证：事件单测。
- [x] 9. `api/procurement.py` 扩展复核列表、resolve 后回写。验证：API 单测。
- [x] 10. 前端 API/事件/任务面板复核区/新状态。验证：源码断言。
- [x] 11. 新增 `tests/test_task_reconciliation.py` 覆盖 AC-1～AC-14。验证：目标测试通过。
- [x] 12. 更新 README；跑回归；回填验收证据。验证：测试输出 + 证据。
