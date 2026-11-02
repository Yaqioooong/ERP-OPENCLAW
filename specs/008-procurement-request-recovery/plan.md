# 技术方案：采购任务请求校验、补充恢复与失败可观测性

- 对应规格：`specs/008-procurement-request-recovery/spec.md`
- 状态：已确认
- 作者：Codex
- 日期：2026-11-02

## 1. 方案选型

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A | 保持主 Agent 不持有恢复工具；任务输入在创建前做共享归一化/结构校验，供应商名称经确定性解析进入补充流程；任务面板直接调用任务 HTTP 恢复接口 | 不扩大对话文字审批面；同一规则覆盖 Agent 工具与 HTTP；保持既有权限边界 | 需要新增共享归一化模块，并让任务面板理解 clarification 中断 | 采用 |
| B | 把 `resume_procurement_task` 重新放回主 Agent 工具池 | 改动集中，对话可直接恢复 | 会重新暴露文字审批/重试入口，削弱“关键操作只用按钮”的设计；仍不能解决面板死路和状态不一致 | 不采用 |

## 2. 总体设计

```text
Agent 工具 / FastAPI
  → 共享请求归一化与结构校验
  → 创建任务 / 进入状态图
  → validate_request：字段规则 + 供应商确定性解析
      ├─ 可唯一解析 → 继续分析
      └─ 缺失/歧义/无效 → need_info + 可展示字段
  → 物料名称/编码确定性解析
      ├─ 唯一匹配 → 回写 part_id → 继续分析
      └─ 缺失/歧义/不存在 → 与其它缺项一次性进入 need_info
  → 任务面板 SupplementPanel
  → POST /api/procurement/tasks/{task_id}/resume {request_patch}
  → Command(resume=...) → validate_request → analyze
```

失败状态以任务记录为终态事实：后台启动异常写入失败后，后续读取不得被较早 Checkpoint 的 `parsing/running` 状态覆盖。前端统一读取 `state_summary.last_error`。

## 3. 改动清单

| 层 | 文件/模块 | 改动类型 | 说明 |
| --- | --- | --- | --- |
| Agent 工具 | `src/agent/tools/procurement_task_tools.py` | 修改 | 使用共享归一化；返回结构化字段错误 |
| Agent 工具 | `src/agent/tools/procurement_task_tools.py` | 修改 | 单物料请求只有品类词时，从最近两轮用户消息恢复唯一明确型号；不回溯无关历史、不生成 ID |
| 领域 | `src/agent/procurement/request_normalization.py` | 新增 | 请求别名归一化、结构预检、补充 patch 合并 |
| 领域模型 | `src/agent/procurement/models.py` | 修改 | 增加用户原始供应商名称字段 |
| 供应商解析 | `src/agent/procurement/data_provider.py`、`ports.py` | 修改 | 按 ID 验证供应商；按名称唯一解析；歧义/不存在返回领域错误；候选生成时用零件 `supplierId` 调用 `supplier_get` 补全主体 |
| 物料解析 | `src/agent/procurement/data_provider.py`、`ports.py`、`nodes.py` | 修改 | 名称先整句模糊查询，无结果时拆出型号/品牌/品类关键词并聚合收敛；唯一解析后回写 `part_id`；歧义、不存在进入待补充，上游故障进入重试 |
| 物料解析优先级 | `src/agent/procurement/data_provider.py` | 修改 | 明确 `part_id` 时直接 `part_get`，名称搜索仅用于没有 ID 的场景；编码可做强一致性校验 |
| 历史契约 | `src/mcp_server/contracts.py` | 修改 | 订单明细保留 `expectedDeliveryDate`；实际交期缺失时供 Agent 计算历史交期回退 |
| 方案数据 | `src/agent/procurement/data_provider.py` | 修改 | 实际交期优先，预计交期回退；供应商、价格和交期分别保留来源，不猜测缺失值 |
| 状态机 | `src/agent/procurement/nodes.py` | 修改 | 字段类型错误和供应商解析失败进入 `need_info`，不再抛出未捕获异常 |
| 可观测性 | `src/agent/procurement/observability.py` | 新增 | 统一节点与路由 trace、耗时、错误摘要和中断记录；使用白名单字段并截断错误文本 |
| 状态机 | `src/agent/procurement/nodes.py` | 修改 | 返回前统一包装全部节点，不改变节点业务逻辑、返回值或异常语义 |
| 工作流 | `src/agent/procurement/workflow.py` | 修改 | 包装条件路由并记录源节点、路由键和目标节点 |
| 解析器 | `src/agent/procurement/parser.py` | 修改 | 补充 patch 先归一化后校验 |
| 任务服务 | `src/agent/procurement/task_runner.py` | 修改 | 启动前预检；重复恢复幂等；终态失败不被旧快照覆盖 |
| 依赖装配 | `src/agent/procurement/dependencies.py` | 修改 | 注入供应商解析器 |
| API | `src/api_view/api/procurement.py` | 修改 | 将启动前结构错误映射为 422 字段错误 |
| 前端 | `frontend/src/components/ProcurementTaskPanel.vue` | 修改 | 渲染补充表单并直接恢复任务；展示真实失败原因、逐项确定性校验错误和退出动作 |
| 前端 | `frontend/src/components/hitl/SupplementPanel.vue` | 修改 | 支持任务补充的可选字段和供应商名称 |
| 前端 | `frontend/src/components/TaskCard.vue` | 修改 | 读取 `last_error`，失败状态显示“查看原因” |
| 前端 | `frontend/src/api/procurement.js` | 修改 | 正确解析结构化 422 错误 |
| 文档 | `README.md` | 修改 | 修正恢复入口说明，记录任务补充契约 |
| 测试 | `tests/test_procurement_request_recovery.py` | 新增 | 覆盖预检、供应商解析、补充恢复、重复提交和终态保护 |
| 测试 | `tests/test_procurement_task_tools.py` | 修改 | 覆盖供应商名称/数字字符串归一化 |

## 4. 接口与数据契约

### 4.1 Agent 工具

| 工具名 | 入参 | 出参 | 错误码/异常 |
| --- | --- | --- | --- |
| `start_procurement_task` | `request.preferred_supplier_id` 可为整数、数字字符串或供应商名称；优先保留原值语义 | 成功返回 `running` 与 `task_id`；等待补充返回任务快照 | 结构错误返回 `status=error` 与字段 errors；供应商歧义不报系统错误，进入 `need_info` |
| `get_procurement_task` | `task_id` | 最新任务状态与 `state_summary.last_error` | 权限与查询错误沿用现状 |
| `resume_procurement_task` | 保持工具实现，但不加入主 Agent 工具池 | 与任务 HTTP 恢复一致 | 仅供内部/未来受控入口 |

### 4.2 HTTP 接口

| 方法 | 路径 | 请求体 | 响应体 | 权限 |
| --- | --- | --- | --- | --- |
| POST | `/api/procurement/tasks` | 现有 `StartTaskRequest` | 成功为任务快照；结构错误为 422 `{code, errors}` | `require_password_ready` |
| POST | `/api/procurement/tasks/{task_id}/resume` | `{resume:{request_patch:{...}}}` 或既有审批/重试 payload | 最新任务快照 | 任务所有者；审批类操作沿用 approver/admin 规则 |
| GET | `/api/procurement/tasks/{task_id}` | 无 | 状态、中断、`state_summary.last_error` | 任务所有者或 admin/approver（沿用现状） |

补充字段新增：

- `preferred_supplier_name: string | null`：用户原始供应商名称，用于追溯和解析。
- `preferred_supplier_id: integer | null`：解析成功后的稳定供应商 ID。
- 外部输入若将名称放在 `preferred_supplier_id`，归一化层转为名称字段，不进行隐式整数转换。

物料字段契约：

- `items[].part_name`、`part_code`、`part_id` 任一项可作为解析线索。
- 组合名称不要求与 ERP 名称连续或同序；先查整句，再按型号优先查询关键词，并按候选跨查询命中次数收敛。
- 唯一解析成功后服务端回写 `items[].part_id` 并保留原名称/编码。
- 歧义/不存在返回 `need_info`，字段为 `items[i].part_id`，错误消息列出候选或可补充线索。
- 物料目录超时/暂不可用进入 `awaiting_retry`，`retry_from=validate_request`；配置或工具缺失等不可恢复故障进入 `failed`。

供应商与交期字段契约：

- 零件记录的 `supplierId` 是稳定业务 ID；候选生成后调用 `supplier_get` 补全 `name/status/credit_rating`。
- 供应商详情读取失败时不得伪造状态；候选保留明确未知状态并交由现有审批警告处理。
- 同一条历史订单的实际交期优先；缺失时使用订单时间到预计交期计算回退交期，并标记 `lead_time_source=expected_delivery`。
- 预计到货日始终由当前日期加候选最大交期计算，不得直接把历史绝对日期当作本次承诺。
- `PLAN_VALIDATION_FAILED.last_error.errors[]` 是失败诊断事实，任务面板必须逐项展示。

对话到工具请求的一致性契约：

- 工具原始请求的 `part_name` 只有品类词，且没有 `part_id/part_code` 时，读取 `ToolRuntime.state.messages` 最近两条用户消息。
- 当前用户消息含唯一字母数字型号时直接恢复；当前消息为确认语时，允许从紧邻的上一条用户消息恢复唯一型号。
- 恢复只追加到原 `part_name`；存在多个型号、非确认语且无型号、多个物料明细时不恢复。
- 工具仍把恢复后的请求交给共享结构校验，不绕开 `part_id` 唯一解析和待补充流程。

物料标识优先级：

- `part_id` 存在时直接读取该物料，不调用名称模糊搜索。
- `part_id` 与 `part_code` 同时存在时，校验正式记录的 `partCode`；不一致返回 `PART_CODE_MISMATCH`，不自动改用另一物料。
- `part_id` 不存在时继续沿用名称/编码模糊解析；只有没有 ID 的请求才依赖搜索结果收敛。

### 4.3 数据模型

- 不新增数据库表或迁移。
- `procurement_task` 现有 `status/last_error_code/last_error_message` 足以保存终态错误。
- 终态状态保护在服务层完成，避免旧快照覆盖；旧数据保持兼容。

## 5. 测试策略

| 验收标准 | 测试类型 | 用例/文件 | 说明 |
| --- | --- | --- | --- |
| AC-1、AC-2、AC-10 | 集成测试 | `tests/test_procurement_request_recovery.py` | 缺失仓库 → 面板等价的 patch resume → 审批中断 |
| AC-3 | 集成测试 | 同上 | 重复提交返回最新状态，不重复执行分析 |
| AC-4 | 集成/权限测试 | 同上及 `tests/test_procurement_auth.py` | 非所有者恢复被拒绝 |
| AC-5、AC-6、AC-7 | 单元/状态机测试 | 同上、`tests/test_procurement_task_tools.py` | 名称唯一解析、歧义进入 need_info、结构错误预检 |
| AC-8、AC-9 | 服务测试 | `tests/test_procurement_request_recovery.py` | 终态失败不被旧 Checkpoint 覆盖 |
| AC-11、AC-12 | 回归 | 采购测试全集、前端 build、pytest 全量 | 审批/下单/核验不回归 |
| AC-13 | 单元/状态机测试 | `tests/test_data_provider.py`、`tests/test_procurement_request_recovery.py` | 名称/编码唯一解析 -> 回写 `part_id` -> 方案生成收到解析后请求 |
| AC-14 | 单元/状态机测试 | 同上 | 歧义/不存在 -> `need_info` 并可恢复；目录超时 -> `awaiting_retry` 并可继续 |
| AC-15 | 状态机测试 | `tests/test_procurement_request_recovery.py` | 物料歧义与缺失仓库在首个中断中同时出现 |
| AC-16 | 单元测试 | `tests/test_data_provider.py` | 零件 `supplierId` 经 `supplier_get` 补全正式名称、状态和信用 |
| AC-17 | 单元/规则测试 | `tests/test_data_provider.py`、`tests/test_plan_proposer.py`、`tests/test_order_tools.py` | MCP 明细保留预计交期；实际交期缺失时使用预计交期；DENSO U27ETR 的 2026-11-10 要求日期可通过校验 |
| AC-18 | 前端构建 | `frontend/src/components/ProcurementTaskPanel.vue` | 面板渲染 `last_error.errors[]`，并执行前端构建验证 |
| AC-19 | 单元/工具契约测试 | `tests/test_procurement_task_tools.py`、`tests/test_data_provider.py` | 验证“火花塞 + 最近确认 U27ETR”恢复为可唯一解析名称，并验证无关/多型号场景不猜测 |
| AC-20 | 单元/规则测试 | `tests/test_data_provider.py`、`tests/test_procurement_request_recovery.py` | `part_get(65)` 成功时即使名称搜索为空也继续；ID/编码冲突进入待补充 |
| AC-21、AC-23 | 日志集成测试 | `tests/test_procurement_observability.py` | 捕获节点与路由日志，验证同一 `task_id` 下可还原 `create_order → verify_order → complete_task` |
| AC-22 | 日志单测 | 同上 | 验证业务失败含错误码、中断与未处理异常有独立事件且原语义不变 |
| AC-24 | 日志安全单测 | 同上 | 验证长错误单行截断，且日志不出现请求正文、偏好文本或完整候选/方案 |

## 6. 风险与回滚

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 供应商模糊查询返回多项 | 用户可能反复补充 | 错误信息列出候选 ID/名称，表单要求填写明确供应商 ID |
| 新字段影响旧请求 | 历史 Checkpoint 缺少名称字段 | 字段有默认空值，模型保持向后兼容 |
| 任务面板重复提交 | 可能重复推进状态图 | 恢复前刷新状态；服务端只允许恢复与当前状态匹配的操作 |
| 终态保护过强 | 合法的后续状态写入受阻 | 仅保护 `completed/rejected/failed/cancelled/orphaned`，非终态流程不受影响 |
| 历史预计交期偏差 | 用旧订单预计值估算当前交期 | 仅在无实际交期时回退，标记来源，并继续由要求到货日规则校验 |
| 供应商详情暂不可用 | 无法补全正式名称或状态 | 保留供应商 ID 和未知状态，不伪造业务事实；现有供应商状态警告继续提示审批人 |
| 日志量或敏感信息增加 | 影响存储成本或泄露业务正文 | 仅记录白名单摘要；错误消息单行化并截断至 500 字符；不为排查输出完整状态对象 |
| 包装层改变状态图行为 | 采购流程回归 | 包装保持原返回值和异常传播；测试中断与异常语义不变，并运行采购回归 |

回滚方式：前后端可独立回退；新增字段默认空值且无数据库迁移，旧任务仍可读取。若需要紧急回滚，恢复面板可先隐藏，但不重新开放主 Agent 的文字审批工具。

## 7. 实施顺序

1. 新增失败测试，固定请求归一化、供应商歧义和终态保护契约。
2. 实现共享归一化、供应商解析及状态机补充处理。
3. 修复任务恢复幂等与终态错误读取。
4. 接入任务面板补充表单和失败展示。
5. 更新 README、回填任务与验收证据，执行相关测试及全量回归。
6. 在请求校验节点加入物料确定性解析，业务性失败聚合到补充中断，上游故障接入正式重试路径。
7. 补齐订单明细预计交期契约与供应商详情补全，修复 DENSO 采购方案的交期校验链路，并在任务面板展示具体校验错误。
8. 在状态图统一边界接入节点与路由日志，覆盖开始、结果、失败、中断和异常，并回填验收证据。
