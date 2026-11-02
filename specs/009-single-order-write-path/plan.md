# 技术方案：订单写入单一路径（workflow-first）

- 对应规格：`specs/009-single-order-write-path/spec.md`
- 状态：已确认
- 作者：实现方
- 日期：2026-11-02

## 1. 方案选型

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A | 保留工作流为唯一创建入口，把订单子 Agent 收窄为“修改/取消/查询” | 改动只在配置与文档层；创建路径保留幂等键、方案哈希、审批 TTL、租约与核验；前端任务卡片链路不变 | 需要同步三处提示词，否则模型仍可能尝试绕过 | 采用 |
| B | 把 `start_procurement_task` 并入订单子 Agent | 表面上“订单域”只有一个所有者 | 子 Agent 是单次 `task` 调用、无后台生命周期；图 interrupt 会嵌套；前端靠 `start_procurement_task` 工具消息取 `task_id`，子 Agent 工具调用对父不可见，任务卡片会断链；且模型仍需重构结构化请求，编造字段风险上升 | 不采用 |

## 2. 总体设计

```text
用户对话
  └─ 主 Agent（编排）
       ├─ 新建采购/下单 ──► start_procurement_task ──► 采购状态下图
       │                        （唯一创建入口）         └─ 审批 → 下单 → 核验
       ├─ 查询任务状态 ───► get_procurement_task
       ├─ 修改/取消/查已有订单 ──► task(procurement-order)
       │                              └─ order_update / order_search_details
       │                                   └─ order_guard：字段校验 + 人工确认
       └─ 采购分析 ───────► task(procurement-analyst)
```

“工具”与“子 Agent”分层：主 Agent 持有的是**工作流入口**（等价于系统能力），
子 Agent 只承担需要模型推理的探索性任务。二者不再争抢“创建订单”这一写操作。

## 3. 改动清单

| 层 | 文件/模块 | 改动类型 | 说明 |
| --- | --- | --- | --- |
| 子 Agent | `src/agent/subagents/configs/procurement_order.yaml` | 修改 | 删除 `order_create`；描述与提示词收窄为存量订单维护；移除加载器不支持的 `interrupt_on` |
| Agent 工具 | `src/agent/tools/order_guard.py` | 不改 | 继续守卫 `order_update`；`order_create` 守卫保留为纵深防御，但已无 LLM 暴露面 |
| 主 Agent | `src/agent/main_agent.py` | 不改 | 任务工具仍挂在主 Agent，链路与前端契约不变 |
| 提示词 | `src/agent/memory/prompts.py` | 修改 | “协调者，不是执行者”改为“对话编排者：流程走工作流、探索走子 Agent”，路由表明确唯一创建入口 |
| 提示词 | `src/agent/memory/AGENTS.md` | 修改 | 更新对话路由、任务分配规则、`procurement-order` 触发词与安全边界 |
| 文档 | `README.md` | 修改 | 架构描述、功能特性与关键设计补充“订单写入单一路径” |
| 测试 | `tests/test_agent_boundaries.py` | 新增 | 锁定 AC-1、AC-2、AC-3、AC-4、AC-5、AC-7 |
| 规格 | `specs/009-single-order-write-path/` | 新增 | 本规格三件套 |

## 4. 接口与数据契约

### 4.1 MCP 工具

无变更。`order_create` 仍由 `McpErpOrderGateway` 调用，入参契约不变。
LLM 侧可见工具变化：`procurement-order` 子 Agent 不再获得 `order_create`。

### 4.2 HTTP 接口

无变更。

### 4.3 数据模型

无变更。

## 5. 测试策略

| 验收标准 | 覆盖方式 |
| --- | --- |
| AC-1 | `test_subagents_never_expose_order_create`：加载全部 YAML，断言无 `order_create` |
| AC-2 | `test_main_prompt_routes_creation_to_workflow`：断言主提示词包含 `start_procurement_task` 与 `procurement-order` 分支 |
| AC-3 | `test_order_subagent_scope_is_existing_orders_only`：断言 `order_update`、`order_search_details` 仍在 |
| AC-4 | `test_update_guard_still_checks_detail_fields`、`test_guard_keeps_update_wrapped_and_read_tools_untouched`：断言 `order_update` 仍被守卫包裹且字段校验生效 |
| AC-5 | 同 AC-3 用例断言 YAML 无 `interrupt_on` |
| AC-6 | 现有 `frontend/src/App.vue` 链路未改；人工回归 |
| AC-7 | 断言订单子 Agent 描述不含“下单”“创建订单” |

命令：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_boundaries.py -q
```

## 6. 风险与回滚

- 风险：模型已习惯把“下单”委派给子 Agent，收窄后若提示词未同步，会出现“子 Agent 拒绝创建、主 Agent 未改道”的空转。
  缓解：三处提示词同批修改，并用测试锁定路由关键词。
- 风险：移除 YAML `interrupt_on` 后若将来加载器支持该字段，可能重新引入第二套审批。
  缓解：在 YAML 中以注释说明“审批由 `order_guard` 触发”，测试断言字段不存在。
- 回滚：本次为配置/文档/测试改动，回滚即恢复 `procurement_order.yaml`、`prompts.py`、`AGENTS.md`、`README.md` 四处文件，无数据迁移。
