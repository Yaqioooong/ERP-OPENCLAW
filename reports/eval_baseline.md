# 采购 Agent 评测报告

- 生成时间：`2026-10-23T16:34:13.835512+08:00`
- 开始时间：`2026-10-23T16:34:13.242679+08:00`
- 场景数：30
- 通过数：30
- 完成率：100.0%
- 规则通过率：100.0%
- 恢复成功率：100.0%
- 重复订单数：0
- 平均工具调用：2.97
- P95 耗时：33 ms

## 分组结果

| group | passed | total |
|---|---:|---:|
| analysis | 5 | 5 |
| approval | 5 | 5 |
| recovery | 5 | 5 |
| report | 5 | 5 |
| requirement | 5 | 5 |
| tool | 5 | 5 |

## 场景明细

| ID | 标题 | 结果 | 状态 | 错误 | 订单数 | 工具调用 | 耗时 |
|---|---|---|---|---|---:|---:|---:|
| RQ-01 | 缺少仓库时应追问 | PASS | need_info | - | 0 | 0 | 19ms |
| RQ-02 | 数量缺失时应追问 | PASS | need_info | - | 0 | 0 | 16ms |
| RQ-03 | 多个物料应全部进入方案 | PASS | awaiting_approval | - | 0 | 6 | 18ms |
| RQ-04 | 修改数量应生成新版本 | PASS | awaiting_approval | - | 0 | 6 | 21ms |
| RQ-05 | 恢复原任务不新建任务 | PASS | completed | - | 1 | 3 | 21ms |
| TL-01 | 正常查询库存 | PASS | awaiting_approval | - | 0 | 3 | 17ms |
| TL-02 | 物料不存在 | PASS | failed | PART_NOT_FOUND | 0 | 1 | 15ms |
| TL-03 | 供应商停用 | PASS | failed | PLAN_VALIDATION_FAILED | 0 | 3 | 16ms |
| TL-04 | 上游异常可恢复 | PASS | awaiting_retry | UPSTREAM_UNAVAILABLE | 0 | 2 | 15ms |
| TL-05 | 查询次数不超过阈值 | PASS | awaiting_approval | - | 0 | 3 | 17ms |
| AN-01 | 库存缺口计算 | PASS | awaiting_approval | - | 0 | 3 | 16ms |
| AN-02 | 总价计算 | PASS | awaiting_approval | - | 0 | 3 | 16ms |
| AN-03 | 报价对比 | PASS | awaiting_approval | - | 0 | 3 | 17ms |
| AN-04 | 交期缺失不伪造 | PASS | awaiting_approval | - | 0 | 3 | 17ms |
| AN-05 | 缺少报价不编造 | PASS | failed | ANALYSIS_FAILED | 0 | 3 | 20ms |
| AP-01 | 审批前不创建订单 | PASS | awaiting_approval | - | 0 | 3 | 24ms |
| AP-02 | 批准后执行 | PASS | completed | - | 1 | 3 | 22ms |
| AP-03 | 拒绝后不下单 | PASS | rejected | - | 0 | 3 | 19ms |
| AP-04 | 修改后重新审批 | PASS | awaiting_approval | - | 0 | 6 | 24ms |
| AP-05 | 无权限审批 | PASS | failed | FORBIDDEN | 0 | 3 | 18ms |
| RC-01 | 库存超时后重试 | PASS | awaiting_approval | - | 0 | 4 | 19ms |
| RC-02 | 审批期间重启 | PASS | completed | - | 1 | 3 | 33ms |
| RC-03 | 订单超时后核验 | PASS | completed | - | 1 | 3 | 20ms |
| RC-04 | 重复提交只创建一单 | PASS | completed | - | 1 | 3 | 20ms |
| RC-05 | 恢复不跳过方案校验 | PASS | completed | - | 1 | 3 | 34ms |
| RP-01 | 报告总价与明细一致 | PASS | awaiting_approval | - | 0 | 3 | 18ms |
| RP-02 | 报告包含供应商对比 | PASS | awaiting_approval | - | 0 | 3 | 17ms |
| RP-03 | 缺失库存标记未知 | PASS | awaiting_approval | - | 0 | 3 | 16ms |
| RP-04 | 报告包含数据来源 | PASS | awaiting_approval | - | 0 | 3 | 16ms |
| RP-05 | 分析失败不生成成功报告 | PASS | failed | PART_NOT_FOUND | 0 | 1 | 15ms |