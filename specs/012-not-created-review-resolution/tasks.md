# 任务清单：确认未创建后的复核状态收口

- 对应规格：`specs/012-not-created-review-resolution/spec.md`
- 状态：已完成

## 方案要点

- 前端：`requiresReview` 排除 `not_created`；新增 `reviewResolved` 已处理区域；保留折叠的“确认已创建”纠错入口。
- 前端：`submitReview` 检查 `task_synced`，任务未收口时提示同步失败。
- 后端：任务读取时对 `not_created` 强制 `requires_manual_review=false`。
- 测试：源码检查 + 任务状态读取测试 + 前端构建。

## 任务

- [x] T1 收口 `not_created` 面板状态
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`
  - 验证：`requiresReview` 排除 `not_created`，已处理区域不再显示“确认未创建”
- [x] T2 处理 `task_synced` 并保留纠错入口
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`
  - 验证：`mark_failed` 后显示已处理；`mark_succeeded` 仍可回转
- [x] T3 后端读取 `not_created` 时清除复核标记
  - 文件：`src/agent/procurement/task_runner.py`
  - 验证：`test_not_created_snapshot_clears_manual_review_flag` 通过
- [x] T4 补回归测试
  - 文件：`tests/test_task_reconciliation.py`
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_task_reconciliation.py -o addopts='' -q`（14 passed）
- [x] T5 前端构建
  - 验证：`cd frontend && npm run build`（通过）
- [x] T6 回填验收证据
  - 文件：`specs/012-not-created-review-resolution/spec.md`

## 完成定义

- [x] `not_created` 不再出现可点击“确认未创建”
- [x] 纠错入口仍可用
- [x] 同步失败有明确提示
- [x] 测试与构建通过
