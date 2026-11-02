# 规格：订单写入单一路径（workflow-first）

- 规格编号：`009`
- 状态：已实现（2026-11-02；需求方在对话中确认 workflow-first 方向）
- 提出人：采购系统负责人
- 日期：2026-11-02
- 关联需求：消除“完整采购任务”与“订单子 Agent”对同一个“创建订单”动作的双重归属

## 1. 背景与目标

系统当前存在两条都能创建采购订单的路径：

1. 主 Agent 调用 `start_procurement_task` → 确定性状态下图（解析 → 分析 → 审批 → 下单 → 核验）；
2. 主 Agent 委派 `procurement-order` 子 Agent → 模型调用带守卫的 `order_create`。

两条路径各有自己的审批交互（任务面板 vs 聊天内 interrupt）和字段校验，文档也互相矛盾
（一处要求“所有订单操作必须经过订单子 Agent”，另一处要求“完整采购任务调用任务工具”）。
同一业务动作有两个所有者，导致模型路由不稳定、审批链路重复、排障时无法判断订单由哪条链路产生。

本次目标：**声明并落实“创建订单只有一条路径”**，即确定性采购任务工作流是唯一的创建入口；
`procurement-order` 子 Agent 只维护已存在的订单（修改、取消、查询）。

## 2. 用户故事

- 作为采购任务发起人，我希望下单只有一种审批方式，以便不会在聊天里和任务面板里看到两套相互独立的下单确认。
- 作为采购任务发起人，我希望“修改/取消已有订单”仍然能直接办理，不必为一个字段变更重新发起整条采购流程。
- 作为系统维护者，我希望从任一子 Agent 的配置就能判断它不会创建订单，以便审计“谁可以写订单”。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 模型侧无创建入口 | 已加载全部子 Agent YAML 配置 | 检查每个子 Agent 的工具列表 | 没有任何子 Agent 暴露 `order_create` |
| AC-2 | 创建订单唯一入口 | 用户要求新建采购单或下单 | 主 Agent 处理该请求 | 只能调用 `start_procurement_task`；提示词与 `AGENTS.md` 均不再要求委派订单子 Agent 去创建订单 |
| AC-3 | 存量订单仍可维护 | 用户要求修改交期、数量、供应商、状态或查询订单明细 | 主 Agent 处理该请求 | 委派 `procurement-order`；该子 Agent 仍持有 `order_update` 与 `order_search_details` |
| AC-4 | 修改订单仍有守卫 | `procurement-order` 收到缺 `order_id` 或缺明细字段的修改请求 | 调用 `order_update` | 执行前触发字段校验与人工确认中断，不得直接调用 ERP 接口（沿用 `order_guard` 现有行为） |
| AC-5 | 审批链不重复 | `procurement-order` 的 YAML 中存在 `interrupt_on`，但加载器不读取该字段 | 订单修改执行 | 审批只由 `order_guard` 触发一次；YAML 不再保留会被误认为生效的 `interrupt_on` 死配置 |
| AC-6 | 任务卡片不断链 | 主 Agent 调用 `start_procurement_task` | 前端刷新任务卡片 | 仍能从对话中的 `start_procurement_task` 工具消息提取 `task_id`（本次不改该链路） |
| AC-7 | 子 Agent 描述不再抢触发词 | 用户说“帮我下单/创建订单” | 主 Agent 路由 | 子 Agent 描述中不出现“下单”“创建订单”等创建类触发词，路由不存在歧义 |

## 4. 范围

**包含：**

- `procurement-order` 子 Agent 的工具清单、描述与系统提示词收窄为“存量订单维护”。
- 主 Agent 系统提示词、`/AGENTS.md`、`README.md` 的路由与安全边界表述统一为 workflow-first。
- 新增回归测试，锁定“任何子 Agent 都不得暴露 `order_create`”与“创建路由指向任务工作流”。

**不包含（Out of Scope）：**

- 不修改采购任务状态图内部逻辑、审批记录、幂等键与核验逻辑。
- 不修改 MCP 工具 `order_create` 本身，也不修改 Java ERP 接口。
- 不把“修改订单”纳入采购任务工作流（后续如需再做独立规格）。
- 不改前端组件；任务面板、`InterruptBanner`、`ApprovalPanel` 保持现状。
- 不调整 `procurement-analyst`。

## 5. 约束与依赖

- 涉及模块：`src/agent/subagents/configs/`、`src/agent/memory/`、`README.md`、`tests/`。
- 依赖服务：无新增依赖；测试不连接 PostgreSQL/Redis/MCP。
- 兼容性：`order_create` 仍由工作流通过 `McpErpOrderGateway` 调用原始 MCP 工具，行为不变。
- 权限：审批仍由服务端任务接口与 `order_guard` 中断承担，前端入口显隐不作为权限边界。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 是否最终退役 `procurement-order`，把订单修改也纳入工作流？ | 影响后续规格范围 | 采购系统负责人 | [待确认] 本次保留，作为存量订单维护入口 |

## 7. 验收证据

| 验收标准 | 证据 |
| --- | --- |
| AC-1 | `tests/test_agent_boundaries.py::test_subagents_never_expose_order_create` 通过 |
| AC-2 | `test_main_prompt_routes_creation_to_workflow`、`test_agents_md_safety_rule_points_to_workflow` 通过 |
| AC-3 | `test_order_subagent_scope_is_existing_orders_only` 通过（`order_update`、`order_search_details` 保留） |
| AC-4 | `test_update_guard_still_checks_detail_fields`、`test_guard_keeps_update_wrapped_and_read_tools_untouched` 通过 |
| AC-5 | `test_order_subagent_scope_is_existing_orders_only` 断言 YAML 不含 `interrupt_on` |
| AC-6 | `frontend/src/App.vue` 未改动；本次未启动前后端联调（需 Redis/PostgreSQL/MCP），任务卡片链路沿用现有实现 |
| AC-7 | `test_order_subagent_description_has_no_create_trigger` 通过 |

命令与结果（2026-11-02）：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_boundaries.py -q
# 7 passed

PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
# 335 passed，退出码 0；1 条既有 UserWarning（api_view/api/chat.py model_kwargs），与本次改动无关
```

