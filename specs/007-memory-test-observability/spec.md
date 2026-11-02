# 规格：记忆中间件测试矩阵与最小可观测指标

- 规格编号：`007`
- 状态：已实现（2026-11-02）
- 前置规格：`specs/003-preference-schema-versioning/spec.md`、`004`、`005`、`006`

## 1. 目标

现有测试偏重采购状态机、审批、恢复和订单执行。本规格补齐记忆子系统的专项测试矩阵，并提供进程内最小指标，使关键失败可以被上层和运维观察。

## 2. 测试矩阵

| 类型 | 场景 | 测试位置 |
| --- | --- | --- |
| 隔离 | A/B 互不可见 | `tests/test_memory_backend.py` |
| 首次使用 | 新用户创建偏好文件 | `tests/test_memory_contract.py` |
| 结构校验 | 非法 YAML/枚举/未知字段 | `tests/test_preference_schema.py`、`tests/test_memory_middleware.py` |
| 显式偏好 | 持久化；自动更新不覆盖 | `tests/test_preference_service.py`、`tests/test_memory_middleware.py` |
| 自动提取 | 去重、限长、误判、跳过规则 | `tests/test_memory_candidates.py`、`tests/test_preference_schema.py` |
| 故障 | 读失败/写失败/锁不可用 | `tests/test_preference_service.py`、`tests/test_memory_metrics.py` |
| 并发 | 两个更新同时发生不丢数据 | `tests/test_preference_service.py` |
| 遗忘 | 单条删除、清空、删除会话不影响偏好 | `tests/test_memory_api.py`、`tests/test_memory_contract.py` |
| 边界 | 记忆不能替代实时供应商/审批校验 | `tests/test_memory_contract.py` |

## 3. 最小指标

进程内 `MemoryMetrics`，提供 `GET /api/memory/metrics`（仅 admin）：

| 指标 | 说明 |
| --- | --- |
| `memory_read_success_total` | 偏好读取成功（含明确的 not-found） |
| `memory_read_failure_total` | 读取异常/错误 |
| `memory_write_success_total` | 提交写入成功 |
| `memory_write_failure_total` | 写入/读取/锁等失败 |
| `memory_write_conflict_total` | revision 冲突重试耗尽 |
| `memory_candidates_total` | 自动记忆候选数量 |
| `memory_candidates_rejected_total{reason=...}` | 规则拒绝的候选 |
| `memory_parse_failure_total` | 偏好文件解析失败 |
| `memory_explicit_save_failure_total` | 显式偏好保存失败 |

- 指标不含偏好正文、供应商名或查询文本。
- 自动记忆日志包含 `user_id/thread_id/request_id/message_ids/revision`，可按来源消息定位。
- 显式保存失败通过工具 `saved=false` 返回给 Agent/用户，不只写日志。
- 记忆写入失败不修改采购任务/审批数据（代码边界测试保证）。

## 4. 验收

- 测试矩阵均有自动化测试。
- 关键失败有上层结果（工具/API）或明确指标。
- 自动更新可按 `user_id/thread_id/message_id` 追溯。
- 记忆失败不影响任务状态。
- 全量测试通过。

## 5. 范围

包含：测试、进程内指标、指标端点、审计 message_ids、测试矩阵文档。
不包含：Prometheus 导出、跨实例指标聚合、测试覆盖率门禁、记忆模块独立 CI 流水线。
