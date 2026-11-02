# 任务清单：采购任务请求校验、补充恢复与失败可观测性

- 对应规格：`specs/008-procurement-request-recovery/spec.md`
- 对应方案：`specs/008-procurement-request-recovery/plan.md`
- 状态：物料解析恢复、方案数据完整性、失败诊断与对话确认上下文规则已实现；环境级全量回归待补

> 规则：先测试，后实现；每完成一条就勾选并记录验证结果。

## 阶段一：契约与测试

- [x] T1 增加请求归一化与启动前校验测试
  - 文件：`tests/test_procurement_task_tools.py`、`tests/test_procurement_request_recovery.py`
  - 覆盖：AC-5、AC-7
  - 验证：相关用例已通过；API 字段错误映射另见 `tests/test_procurement_auth.py`

- [x] T2 增加缺失仓库恢复、供应商歧义、重复提交与终态保护测试
  - 文件：`tests/test_procurement_request_recovery.py`
  - 覆盖：AC-1、AC-2、AC-3、AC-6、AC-8、AC-10
  - 验证：内存状态机用例通过；PostgreSQL 集成用例已编写但因本机测试库不可用而跳过

## 阶段二：后端实现

- [x] T3 增加共享请求归一化与字段级校验错误
  - 文件：`src/agent/procurement/request_normalization.py`、`src/agent/tools/procurement_task_tools.py`
  - 覆盖：AC-2、AC-7
  - 验证：T1 通过

- [x] T4 增加供应商确定性解析并接入请求校验节点
  - 文件：`src/agent/procurement/models.py`、`ports.py`、`data_provider.py`、`nodes.py`、`dependencies.py`、`parser.py`
  - 覆盖：AC-5、AC-6
  - 验证：供应商唯一/歧义/不存在测试通过

- [x] T5 修复恢复幂等和任务终态一致性
  - 文件：`src/agent/procurement/task_runner.py`
  - 覆盖：AC-3、AC-8、AC-10
  - 验证：恢复和快照测试通过

- [x] T6 映射 API 结构错误与恢复契约
  - 文件：`src/api_view/api/procurement.py`
  - 覆盖：AC-4、AC-7
  - 验证：API 测试通过

## 阶段三：前端集成

- [x] T7 在任务面板接入补充表单与恢复按钮
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`、`frontend/src/components/hitl/SupplementPanel.vue`
  - 覆盖：AC-1、AC-2、AC-9
  - 验证：前端 build 通过

- [x] T8 修复失败状态展示、卡片文案和结构化错误解析
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`、`TaskCard.vue`、`frontend/src/api/procurement.js`
  - 覆盖：AC-8、AC-9
  - 验证：前端 build 通过

## 阶段四：验收

- [x] T9 更新 README 的采购任务恢复与供应商规则说明
  - 文件：`README.md`
  - 覆盖：AC-5、AC-6、AC-10
  - 验证：文档与接口测试一致

- [ ] T10 回填验收证据并运行全量回归
  - 文件：`specs/008-procurement-request-recovery/spec.md`、本文件
  - 覆盖：AC-1 至 AC-12
  - 验证：验收证据已回填；前端 build 通过，采购集 `37 passed, 24 skipped`，非 Redis 全量集 `193 passed, 57 skipped`；全量集仍受本机 Redis/PostgreSQL 环境阻塞

## 阶段五：物料解析恢复（2026-11-02 确认补充）

- [x] T11 增加物料名称/编码自动解析与回写测试
  - 文件：`tests/test_data_provider.py`、`tests/test_procurement_request_recovery.py`
  - 覆盖：AC-13
  - 验证：名称、编码唯一解析及方案前 `part_id` 回写用例通过

- [x] T12 增加物料歧义、缺失与一次性列齐测试
  - 文件：`tests/test_procurement_request_recovery.py`
  - 覆盖：AC-14、AC-15
  - 验证：物料歧义与缺仓库在同一中断中列出，填写 ID 后原任务继续至审批

- [x] T13 在请求校验节点接入物料确定性解析
  - 文件：`src/agent/procurement/ports.py`、`data_provider.py`、`nodes.py`
  - 覆盖：AC-13、AC-14、AC-15
  - 验证：`PartResolution` / `PartResolutionError` 契约与 MCP 适配器已接入

- [x] T14 将物料解析上游故障接入正式重试路径
  - 文件：`src/agent/procurement/nodes.py`、`routing.py`、`workflow.py`
  - 覆盖：AC-14 的“非用户可修复”分支
  - 验证：目录超时进入 `awaiting_retry`，`retry_from=validate_request`；重试后原任务继续至审批

- [x] T15 支持品牌、型号、品类组合名称的模糊检索回退
  - 文件：`src/agent/procurement/data_provider.py`、`tests/test_data_provider.py`
  - 覆盖：AC-13
  - 验证：整句 `DENSO U27ETR 火花塞` 查询为空时，按 `U27ETR`、`DENSO`、`火花塞` 继续查询并从三个火花塞候选中唯一收敛到 ERP 物料 65

## 阶段六：方案数据完整性与失败诊断（2026-11-02 确认补充）

- [x] T16 增加供应商补全、预计交期回退和具体校验错误测试
  - 文件：`tests/test_data_provider.py`、`tests/test_plan_proposer.py`、`tests/test_order_tools.py`
  - 覆盖：AC-16、AC-17、AC-18
  - 验证：新增用例先复现 5 个失败点，实现后相关测试 `30 passed`

- [x] T17 扩展订单明细契约并保留预计交期
  - 文件：`src/mcp_server/contracts.py`
  - 覆盖：AC-17
  - 验证：`normalize_order_detail()` 输出 `expected_delivery_date`，订单工具测试通过

- [x] T18 接入供应商补全与历史交期回退
  - 文件：`src/agent/procurement/data_provider.py`
  - 覆盖：AC-16、AC-17
  - 验证：`supplierId=31` 输出正式供应商信息；实际交期优先、预计交期回退并标记来源；DENSO 方案预计到货 2026-11-10 并通过校验

- [x] T19 展示确定性校验的具体错误
  - 文件：`frontend/src/components/ProcurementTaskPanel.vue`
  - 覆盖：AC-18
  - 验证：失败面板逐项展示 `last_error.errors[]`；`npm run build --prefix frontend` 通过

## 阶段七：对话确认上下文一致性（2026-11-02 确认补充）

- [x] T20 增加已确认型号丢失的复现与防猜测测试
  - 文件：`tests/test_procurement_task_tools.py`
  - 覆盖：AC-19
  - 验证：6 个新增用例通过，覆盖确认恢复、当前消息恢复、工具到 Runner 集成、无确认不恢复、多型号不恢复和多物料不恢复

- [x] T21 在采购任务工具入口恢复最近确认的唯一型号
  - 文件：`src/agent/tools/procurement_task_tools.py`、`src/agent/memory/prompts.py`
  - 覆盖：AC-19
  - 验证：恢复后的 `part_name` 为“火花塞 U27ETR”并传入 Runner；系统提示词明确禁止只传品类词；相关采购回归 `50 passed, 24 skipped`

## 阶段八：明确物料 ID 的解析优先级（2026-11-02 确认补充）

- [x] T22 增加“名称搜索为空但 part_id 有效”和编码冲突测试
  - 文件：`tests/test_data_provider.py`
  - 覆盖：AC-20
  - 验证：先复现明确 `part_id=65` 仍返回 `PART_NOT_FOUND`；实现后两个用例通过，编码冲突返回 `PART_CODE_MISMATCH`

- [x] T23 调整物料解析顺序为 ID 优先
  - 文件：`src/agent/procurement/data_provider.py`
  - 覆盖：AC-20
  - 验证：名称存在时不再阻断 `part_get`；无 ID 的名称/编码解析行为保持不变；相关采购回归 `52 passed, 24 skipped`

## 阶段九：状态图节点诊断日志（2026-11-02 确认补充）

- [x] T24 增加节点、路由、中断和错误日志测试
  - 文件：`tests/test_procurement_observability.py`
  - 覆盖：AC-21、AC-22、AC-23、AC-24
  - 验证：先复现缺少 `observability` 模块；实现后 6 个用例通过，成功链路、下单失败分支、失败码、中断/异常语义和脱敏截断均有覆盖

- [x] T25 实现统一节点 trace 包装
  - 文件：`src/agent/procurement/observability.py`、`src/agent/procurement/nodes.py`
  - 覆盖：AC-21、AC-22、AC-24
  - 验证：全部节点自动记录开始、结果、失败、中断或异常；不改变业务返回值、异常和 interrupt 传播

- [x] T26 实现条件路由 trace
  - 文件：`src/agent/procurement/observability.py`、`src/agent/procurement/workflow.py`
  - 覆盖：AC-23
  - 验证：实际分支记录源节点、路由键和目标节点；正常采购可还原 `create_order → verify_order → complete_task` 并看到订单号

- [x] T27 执行采购回归并回填验收证据
  - 文件：`specs/008-procurement-request-recovery/spec.md`、本文件
  - 覆盖：AC-21、AC-22、AC-23、AC-24
  - 验证：日志测试通过；采购回归 `58 passed, 24 skipped`；非 Redis/PostgreSQL 全量回归 `212 passed, 57 skipped`；`git diff --check` 通过

## 完成定义（DoD）

- [x] 所有验收标准有对应测试或可复现验证记录
- [ ] 全量测试和前端构建通过（前端通过；非 Redis/PostgreSQL 全量集 `206 passed, 57 skipped`，其余等待可用环境）
- [x] 接口契约与 README 一致
- [x] 无数据库迁移；回滚方式明确
- [ ] 提交信息包含 `feat(008):`
