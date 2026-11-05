# 规格：工具调用异常与超时兜底

- 规格编号：`011`
- 状态：已确认（2026-11-04，按推荐默认值实施）
- 提出人：采购 Agent 使用者
- 日期：2026-11-04
- 关联需求：失败降级专项 P0-7；`specs/010-delegation-contract` 的运行时边界

## 1. 背景与目标

主 Agent 和两个采购子 Agent 都通过工具访问 MCP/ERP、沙箱和外部服务。当前
LangGraph `ToolNode` 默认只处理参数校验类 `ToolInvocationError`，其他异常
（httpx 超时、连接断开、MCP 传输失败、沙箱异常、Redis 读记忆失败等）会
重新抛出，导致：

- 一个只读查询超时，整个子 Agent 回合失败；
- 子 Agent 失败后被 010 委派契约兜住，但一个工具失败仍然浪费整个子任务；
- 写订单工具异常时，父 Agent 无法判断“是否已经写成功”，存在重复修改风险；
- 长工具调用没有上限，可能长时间占住回合。

本次目标：在工具调用边界统一加超时、有限重试和结构化错误回传，让单个工具
失败变成模型可消费的 tool error，而不是打断整个回合；写操作绝不自动重试。

## 2. 用户故事

- 作为采购分析使用者，我希望某次供应商/库存查询超时后，子 Agent 能换一种查询或
  告知我部分数据不可用，而不是整个分析失败。
- 作为订单维护使用者，我希望订单写工具超时或异常时明确提示“结果未知，先查询”，
  而不是静默重试导致重复修改。
- 作为排障人员，我希望每次工具失败都有稳定错误码、是否可重试、是否可能已产生
  副作用、重试次数和耗时，且日志不包含工具参数/结果正文。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 读工具超时 | 只读工具超过配置时限 | 工具调用 | 最多尝试 2 次（1 次重试）；返回 `ToolMessage(status=error)`，信封 `status=timeout`、`code=TOOL_TIMEOUT`、`retryable=true`；不抛出到回合 |
| AC-2 | 读工具瞬时异常 | 连接断开/429/传输失败 | 工具调用 | 按策略重试；成功后正常返回；仍失败则结构化错误，`retryable=true` |
| AC-3 | 读工具非瞬时异常 | 参数/业务/未知异常 | 工具调用 | 不重试，返回 `code=TOOL_FAILED`、`retryable=false` |
| AC-4 | 写工具超时 | `order_create`/`order_update`/`execute` 等写工具超时 | 工具调用 | 不重试；`status=timeout`、`side_effect_unknown=true`、`retryable=false`；提示先查询/确认状态 |
| AC-5 | 写工具异常 | 写工具抛异常 | 工具调用 | 不重试；`status=error`、`side_effect_unknown=true`、`retryable=false` |
| AC-6 | 成功调用 | 工具正常返回 | 工具调用 | 原样返回，不改变工具结果 |
| AC-7 | HITL 中断 | 工具内部触发 `GraphInterrupt` | 工具调用 | 原样传播，不包装成普通错误 |
| AC-8 | 取消 | 父请求取消 | 工具调用中 | `CancelledError` 原样传播 |
| AC-9 | 跳过 `task` | 调用的是委派工具 | 工具调用 | 不重复包装；由 010 委派契约处理 |
| AC-10 | 超时策略 | 不同工具 | 工具调用 | `execute` 300s；图表/压缩/技能/下载 120s；订单写 120s；默认 45s；均可配置 |
| AC-11 | 注册范围 | 主 Agent / 采购分析 / 订单子 Agent | 构建 | 三个 Agent 的中间件栈都包含工具兜底；`task` 只在主 Agent 跳过 |
| AC-12 | 可观测 | 任意工具失败 | 结束 | 记录 `tool.call.*` 日志，含 `tool/status/code/retryable/side_effect_unknown/attempts/duration_ms`；不记录参数和结果正文 |
| AC-13 | 不改变业务语义 | 工具正常/业务失败 | 调用 | 不改变订单守卫、审批、唯一写路径和既有 MCP 错误信封 |
| AC-14 | 同步路径 | 同步 `invoke` | 工具异常 | 至少有异常兜底；同步路径不强制超时 |

## 4. 范围

**包含：**

- 主 Agent、`procurement-analyst`、`procurement-order` 的工具调用边界。
- 超时、有限重试、结构化错误 ToolMessage、写操作副作用未知标记。
- 日志与可配置策略。
- 单测 + 真实 `ToolNode` 集成测试。

**不包含：**

- 修改 MCP 服务端错误码契约、订单守卫、审批或工作流。
- 断路器、熔断、跨进程限流、Prometheus 指标（后续 spec）。
- 对 MCP 返回 `ok=false` 的业务错误做二次重试。
- 为 `general-purpose` 内置子 Agent 注入（当前不受 `extra_middleware` 控制）。

## 5. 约束与依赖

- 涉及模块：`src/agent/middlewares/`、`src/agent/middleware_config.py`、`src/agent/main_agent.py`、`tests/`。
- LangGraph `ToolNode` 默认只处理 `ToolInvocationError`；本规格在中间件层兜底。
- 订单写工具仍受 `order_guard`、HITL、唯一创建入口约束；兜底层不得绕过。
- 同步路径无法可靠取消正在运行的线程；只做异常兜底，超时仅在异步路径强制。
- 重试只针对只读、瞬时错误；重试期间不改变请求参数。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 默认超时？ | 长任务体验 | 需求方 | 已确认：默认 45s；`execute` 300s；图表/压缩/技能/下载/订单写 120s |
| Q-2 | 读工具重试次数？ | 延迟与恢复率 | 需求方 | 已确认：最多 2 次尝试（1 次重试），退避 0.25s |
| Q-3 | 写工具是否自动重试？ | 重复副作用 | 需求方 | 已确认：不自动重试，`side_effect_unknown=true` |
| Q-4 | 哪些算写工具？ | 策略边界 | 需求方 | 已确认：`order_create/order_update`、`execute`、`write_file/edit_file`、`assign_skill`、`save_user_preferences`、`download_sandbox_file`、`start_procurement_task`、`compact_conversation`、`write_todos`、`generate_spreadsheet` |
| Q-5 | `task` 是否包装？ | 与 010 重复 | 需求方 | 已确认：主 Agent 跳过 `task`，由 010 委派契约处理 |

## 7. 验收证据

2026-11-04 实施证据：

- 单测（12 passed）：`tests/test_tool_resilience.py`
  - 读工具超时：尝试 2 次后返回 `status=timeout`、`TOOL_TIMEOUT`、`retryable=true`；
  - 读工具连接异常：重试一次后成功；
  - 读工具非瞬时异常：不重试，`TOOL_FAILED`、`retryable=false`；
  - 写工具超时：只调用 1 次，`side_effect_unknown=true`、`retryable=false`，
    提示先用 `order_search_details` 查询；
  - 写工具异常：只调用 1 次，标记副作用未知；
  - `GraphInterrupt`/`CancelledError` 原样传播；
  - `task` 跳过；成功结果原样返回；同步异常兜底；
  - 真实 `ToolNode` 路径：工具异常转为 error ToolMessage，不向回合外抛出；
  - 静态断言主 Agent 与两个采购子 Agent 都已注册中间件。
- 目标回归（45 passed）：`tests/test_tool_resilience.py`（12）、
  `tests/test_delegation_contract.py`（23）、`tests/test_delegation_runtime.py`（3）、
  `tests/test_agent_boundaries.py`（7）。
- 文档：`README.md`「工具调用异常与超时」、`src/agent/memory/AGENTS.md`
  「工具调用失败边界」已与实现对齐。

已知限制：

- `general-purpose` 是 deepagents 内置子 Agent，不走 `extra_middleware`，本次未注入；
- 同步 `wrap_tool_call` 只做异常兜底，不强制超时；异步路径才执行超时；
- 当前只处理“异常/超时”，不消费 MCP 返回的 `ok=false` 业务错误信封做二次重试；
- 未实现断路器/熔断/跨进程限流，属于后续 spec；
- 写工具异常一律保守标记 `side_effect_unknown=true`，可能把未产生副作用的失败也提示为需确认。
