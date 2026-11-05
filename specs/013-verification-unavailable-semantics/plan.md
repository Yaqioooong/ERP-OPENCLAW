# 技术方案：订单核验不可用与结果未知的状态语义

- 对应规格：`specs/013-verification-unavailable-semantics/spec.md`
- 状态：已实现（2026-11-05）
- 作者：实现方
- 日期：2026-11-05

## 1. 方案选型

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A | 保留 `UNKNOWN`，新增 `ORDER_VERIFY_UNAVAILABLE` 错误码和独立计数 | 改动集中；兼容现有状态机；不新增执行状态 | 需要新增数据库列和错误码分支 | 采用 |
| B | 新增 `UNAVAILABLE` 执行状态 | 语义最清晰 | 影响所有执行状态分支、查询和前端 | 不采用 |
| C | 502 直接判 `failed` | 简单 | 账实不一致，可能丢失已创建订单 | 不采用 |

## 2. 总体设计

```text
ERP get_by_number 502/timeout
  -> GatewayUnavailable / GatewayTimeout / GatewayBusinessError
  -> VerificationOutcome.UNAVAILABLE
  -> execution.status = UNKNOWN
  -> last_error_code = ORDER_VERIFY_UNAVAILABLE
  -> verification_unavailable_attempts += 1
  -> task.status = awaiting_retry
  -> 面板显示“重试核验”，不显示“确认未创建”
```

```text
查询成功但订单为空
  -> VerificationOutcome.UNKNOWN（not_found_yet）
  -> verification_attempts += 1
  -> 达到确认窗口后 ORDER_NOT_FOUND_CONFIRMED
  -> task.status = not_created
```

```text
awaiting_retry + execution_status=unknown
  -> 用户取消
  -> task.status = awaiting_review
  -> last_error_code = ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL
```

## 3. 改动清单

| 层 | 文件/模块 | 改动类型 | 说明 |
| --- | --- | --- | --- |
| 领域模型 | `src/agent/procurement/models.py` | 修改 | 新增 `VerificationOutcome.UNAVAILABLE`；`ExecutionRecord.verification_unavailable_attempts` |
| 执行仓储 | `src/agent/procurement/execution_repository.py` | 修改 | 新列、兼容迁移、`mark_verification_unavailable` |
| 对账 | `src/agent/procurement/order_reconciliation.py` | 修改 | 502/超时走独立计数和 `ORDER_VERIFY_UNAVAILABLE`；`UNAVAILABLE` 不计入未找到 |
| 任务回写 | `src/agent/procurement/task_reconciliation.py` | 修改 | `UNAVAILABLE` 不改任务状态 |
| 状态图 | `src/agent/procurement/nodes.py` | 修改 | 未知结果取消进入 `awaiting_review` |
| 前端 | `frontend/src/components/ProcurementTaskPanel.vue`、`frontend/src/components/TaskCard.vue` | 修改 | 区分 cancelled 与失败；取消提示人工核对 |
| 测试 | `tests/test_order_reconciliation.py`、`tests/test_procurement_recovery.py`、`tests/test_task_reconciliation.py` | 修改 | 502 计数、确认窗口、取消状态、前端源码检查 |
| 文档 | `README.md`、`specs/013-...` | 修改 | 状态语义与验收证据 |

## 4. 接口与数据契约

### 4.1 数据模型

新增列：

```sql
ALTER TABLE procurement_order_execution
    ADD COLUMN IF NOT EXISTS verification_unavailable_attempts INTEGER NOT NULL DEFAULT 0;
```

`ExecutionRecord` 新增：

```python
verification_unavailable_attempts: int = 0
```

### 4.2 错误码

| 错误码 | 含义 | 任务状态 |
| --- | --- | --- |
| `ORDER_VERIFY_UNAVAILABLE` | 502/超时/连接失败，无法核验 | `awaiting_retry` |
| `ORDER_RESULT_UNKNOWN` | 查询成功但结果暂不确定 | `awaiting_retry` |
| `ORDER_NOT_FOUND_CONFIRMED` | 成功空查询达到确认窗口 | `not_created` 或同步路径 `failed` |
| `ORDER_OUTCOME_UNKNOWN_AFTER_CANCEL` | 结果未知时用户停止任务 | `awaiting_review` |

### 4.3 任务接口

- `GET /api/procurement/tasks/{task_id}` 返回的 `state_summary.last_error.code` 使用新错误码。
- `GET /api/procurement/executions/unknown` 继续返回待对账记录；新增计数可作为诊断字段。

## 5. 测试策略

| 验收标准 | 测试类型 | 用例/文件 | 说明 |
| --- | --- | --- | --- |
| AC-1、AC-3 | 单元 | `tests/test_order_reconciliation.py` | 502 后错误码和独立计数 |
| AC-2 | 单元 | `tests/test_order_reconciliation.py` | 502 不推进未找到确认 |
| AC-4、AC-5 | 集成 | `tests/test_procurement_recovery.py` | 未知取消进入 awaiting_review；普通取消仍 cancelled |
| AC-6、AC-7 | 前端源码/构建 | `tests/test_task_reconciliation.py`、`npm run build` | cancelled 文案不显示失败 |
| AC-8 | 回归 | `tests/test_task_reconciliation.py` | 人工复核不变 |
| AC-9 | 数据库 | `tests/test_order_reconciliation.py` | 新列默认值兼容 |
| AC-10 | 事件 | `tests/test_procurement_recovery.py` | 取消事件状态与任务一致 |

## 6. 风险与回滚

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 新增列失败 | 服务启动失败 | `ADD COLUMN IF NOT EXISTS`；测试库验证 |
| 旧记录计数污染 | 旧任务可能仍过早确认 | 不回填历史；只保证新记录；排查时看新计数 |
| 取消语义变化 | 用户以为取消即结束 | 页面提示“订单可能已创建，待人工核对” |
| 502 持续 | 任务长期 awaiting_retry | 保留重试/转人工；不自动判未创建 |

回滚方式：代码回滚后新列可保留，不影响旧逻辑；任务状态存量数据保持原样。

## 7. 实施顺序

1. 模型与执行仓储新增字段和方法。
2. 对账区分 UNAVAILABLE 与 UNKNOWN。
3. 状态图取消未知任务进入人工复核。
4. 前端取消文案与入口。
5. 测试、迁移验证、构建与文档。
