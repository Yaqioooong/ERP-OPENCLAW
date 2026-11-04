# 规格：委派内容结构化与失败兜底

- 规格编号：`010`
- 状态：已确认（2026-11-04）
- 提出人：采购 Agent 使用者
- 日期：2026-11-04
- 关联需求：失败降级专项 P0-6；README「委派与回传契约」

## 1. 背景与目标

主 Agent 通过 deepagents 的 `task` 工具把采购分析、存量订单维护委派给子 Agent。当前下行只是一段自由文本，靠提示词约定【任务目标】【用户偏好】【需求正文】【输出要求】四段；上行只取子 Agent 最后一条有文本的 AI 消息。仓库里已经有契约解析/渲染代码和单测，但运行时没有接入；并且当前框架的 `task` 返回 `Command` 而不是 `ToolMessage`，现有中间件即使注册也只会原样透传。结果是：

- 简报缺关键段落时，子 Agent 仍可能被派发，只能靠它自己猜；
- 子 Agent 抛异常、超时、返回空文本或残缺文本时，父回合可能整体失败，或者把空/残缺文本当成结论；
- 父 Agent 拿到的是自由文本，无法稳定判断“成功了没有、能不能重试、有没有文件产物、有没有副作用未知”；
- README 已把该能力描述为生效，但 `specs/010-delegation-contract/` 为空，缺少验收证据。

本次目标：把委派的下行简报和上行回传都收敛成**可校验、可降级、可追踪的结构化契约**；任何子 Agent 失败都转化为父 Agent 能理解和决策的结构化结果，不再打断主回合。

## 2. 用户故事

- 作为采购分析使用者，我希望委派前的简报被校验和补齐，缺关键信息时立即得到可修复提示，而不是让子 Agent 拿到残缺简报后猜。
- 作为主 Agent，我希望子 Agent 的返回总是同一套结构：成功、降级、失败、超时，并明确是否可重试、是否有文件产物、是否有副作用未知。
- 作为订单维护使用者，我希望订单写操作超时后不会被自动重试，而是明确提示“结果未知，先查询订单”，避免重复修改。
- 作为排障人员，我希望每次委派都有稳定状态、错误码、耗时和子 Agent 标识，且日志不包含简报正文、偏好正文或原始工具输出。

## 3. 验收标准

### 3.1 下行：简报结构化

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 缺必需段 | `description` 缺【任务目标】或【需求正文】，或正文为空 | 主 Agent 调用 `task` | 不派发子 Agent；父 Agent 收到 `status=error`、`code=DELEGATION_BRIEF_INVALID`、`missing[]`、`retryable=true`，并给出可修复提示；不得产生子 Agent 调用 |
| AC-2 | 可补齐段 | 缺【用户偏好】或【输出要求】 | 主 Agent 调用 `task` | 用可信运行时上下文（`user_id`、`username`、偏好文件路径）和该子 Agent 的固定输出形态补齐；子 Agent 实际收到的简报包含四段；补齐事件可追踪 |
| AC-3 | 结构化解析 | 模型输入四段文本，或未来兼容的 JSON 简报 | 系统处理 `task` 入参 | 两种输入都解析为同一个结构化简报对象；子 Agent 看到的仍是规范四段文本，不改变现有子 Agent 提示词契约 |
| AC-4 | 未知子 Agent | `subagent_type` 不在已加载配置中 | 主 Agent 调用 `task` | 不派发；返回 `status=error`、`code=DELEGATION_AGENT_UNKNOWN`、`available_agents[]`，模型可改用正确类型重试 |
| AC-5 | 非 task 工具 | 调用的是其他工具 | 中间件处理 | 不改变请求和返回值，零副作用 |

### 3.2 上行：回传结构化与降级

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-6 | 成功回传 | 子 Agent 返回完整规范段落 | `task` 完成 | 父 Agent 收到 `status=ok`、`agent`、`fields`（四段）、`report_path`（如有）、`duration_ms`；原文不丢失；不得把原始工具输出塞进信封 |
| AC-7 | 缺段降级 | 子 Agent 有文本但缺必需段落 | `task` 完成 | 返回 `status=degraded`、`missing[]`、`raw_text`（截断上限可配置）、可提取的 `report_path`；明确提示“先 read_file 读文件产物再下结论”；父回合不崩溃 |
| AC-8 | 空返回 | 子 Agent 没有文本 | `task` 完成 | 返回 `status=error` 或 `degraded`（实现时固定一种，并写入 plan）、稳定错误码 `DELEGATION_EMPTY_RESULT`、`retryable=true`；不得当成功 |
| AC-9 | 子 Agent 异常 | 子 Agent 内部抛异常（限流、断连、递归超限等） | `task` 执行 | 父 Agent 收到 `status=error`、稳定错误码、`retryable` 按错误分类、可读消息；主回合继续；不把堆栈或内部敏感信息回给模型 |
| AC-10 | 子 Agent 超时 | 子 Agent 超过配置时限 | `task` 执行 | 返回 `status=timeout`、`retryable=true`；分析类任务可重试；订单写类任务额外带 `side_effect_unknown=true`、`retryable=false`，提示先查询订单再决定 |
| AC-11 | 业务失败 | 子 Agent 完整返回，但【操作结果】为“失败” | `task` 完成 | 信封结构为 `status=ok`、`outcome=failure`（或实现时固定的等价字段），父 Agent 能区分“契约完整”与“业务成功” |
| AC-12 | 中断透传 | `task` 内部触发 HITL 中断 | `task` 返回 | 中断语义原样传播，不被包装成普通失败，不被超时逻辑吞掉 |
| AC-13 | 取消透传 | 父请求被取消 | `task` 执行中 | `CancelledError` 按取消语义传播，不转成普通错误信封 |

### 3.3 运行时接入与契约约束

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-14 | 真实链路接入 | 真实 agent 图已构建 | 主 Agent 调用 `task` | 契约逻辑在真实运行时生效；用集成测试证明（不是只测中间件纯函数），且覆盖当前框架 `Command` 返回形态 |
| AC-15 | 权限不绕过 | 契约层处理委派 | 子 Agent 调用订单工具 | 不新增/绕过 `order_guard`、人工审批和“订单创建唯一入口”；契约层只处理简报与回传 |
| AC-16 | 不自动重试写操作 | 订单写类任务超时或异常 | 父 Agent 收到失败信封 | 契约层不得自动重新调用子 Agent；`retryable=false` 且 `side_effect_unknown=true`，由父 Agent 引导用户先查询 |
| AC-17 | 可观测且不泄密 | 任意委派结束 | 写日志/指标 | 记录 `delegation.result`，字段至少含 `subagent/status/code/retryable/duration_ms/has_report_path`；不记录简报正文、偏好正文、原始工具输出 |
| AC-18 | 文档一致 | 规格实现完成 | 检查 README 与 AGENTS.md | 文档描述的行为与运行时一致；未实现项不得写成已生效；补齐验收证据 |

### 3.4 兼容与边界

- 现有四段格式、常见同义段头（【结论】【建议】【摘要】等）继续被接受；旧子 Agent YAML 不需要修改。
- 信封带版本号，后续字段扩展不破坏旧消费方。
- 新增逻辑不引入外部网络调用；超时上限可配置；日志字段受白名单约束。
- 同步与异步调用都要有异常兜底；超时只在异步路径强制实施（同步路径至少要有异常兜底，限制写入 plan 的风险说明）。

## 4. 范围

**包含：**

- 下行简报的解析、校验、确定性补齐和规范化渲染。
- 上行回传的解析、成功/降级/失败/超时分类和结构化信封。
- 运行时接入，兼容当前框架的 `Command` 返回形态。
- 子 Agent 异常、超时、空返回、未知类型的失败兜底。
- 可配置超时、错误码、重试语义、副作用未知标记。
- 结构化日志/指标，以及与 README、AGENTS.md 的对齐。
- 纯函数单测 + 真实 `task` 链路集成测试。

**不包含（Out of Scope）：**

- 重写 `task` 工具的参数 schema（除非未决问题 Q-1 选择方案 B）。
- 修改子 Agent 的业务提示词、工具权限或订单写规则。
- 自动重试订单写操作。
- 为委派结果新增独立前端页面（父 Agent 以结构化信封呈现即可）。
- 修改 deepagents/langgraph 框架源码。

## 5. 约束与依赖

- 涉及模块：`src/agent/middlewares/`、`src/agent/subagents/`、`src/agent/main_agent.py`、`src/agent/memory/AGENTS.md`、`src/agent/memory/prompts.py`、`tests/`。
- 依赖：deepagents 的 `task` 工具、LangGraph 的 `Command`/中断语义、运行时 `ProcurementContext`。
- 当前框架行为：`task` 成功时返回 `Command(update={"messages": [ToolMessage(...)]})`，不是直接返回 `ToolMessage`；契约层必须在真实返回形态上生效。
- 身份必须是服务端认证后的 `ProcurementContext`，补齐段落只能使用可信字段。
- 订单写路径必须保持 `order_guard` + 人工审批 + 幂等键，不允许契约层绕过。
- 兼容性：不改变子 Agent 看到的四段文本格式；不要求用户改配置。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 下行结构化做到哪一层？A. 边界结构化：模型仍传四段文本，系统解析成简报对象、校验补齐后再给子 Agent；B. 入参结构化：把 `task` 换成带 schema 的委派工具，模型必须按字段传 | 决定改动范围和模型行为风险 | 需求方 | 已确认：A（兼容现有子 Agent 提示词，风险低） |
| Q-2 | 上行信封格式？A. 版本化 JSON 信封（含 `status/agent/retryable/fields/raw_text/report_path`）；B. 纯文本四段；C. JSON + 人读摘要 | 决定父 Agent 的消费方式和测试断言 | 需求方 | 已确认：C（JSON 信封 + 一行状态头/人读提示，兼顾机器断言与模型理解） |
| Q-3 | 超时默认值？分析类 600s、订单类 120s，是否接受？ | 决定长任务体验与误杀边界 | 需求方 | 已确认：分析 600s，订单 120s，均可配置 |
| Q-4 | 分析类读任务失败是否自动重试一次？ | 自动重试可能放大延迟/成本；不自动则依赖父模型重派 | 需求方 | 已确认：不自动重试，只返回 `retryable`，由父模型决定 |
| Q-5 | 订单写类超时/异常的文案和后续动作？ | 决定是否会重复修改订单 | 需求方 | 已确认：`side_effect_unknown=true`、`retryable=false`，提示先 `order_search_details` 查询，不自动重试 |
| Q-6 | `raw_text` 截断上限？ | 影响上下文长度与排障信息量 | 需求方 | 已确认：2000 字符，可配置 |

## 7. 验收证据

2026-11-04 实施证据：

- 单测（23 passed）：`tests/test_delegation_contract.py`
  - 下行：缺必需段不派发、空段视为缺失、缺可补齐段用 runtime context 补齐、JSON 简报兼容、未知子 Agent 拦截；
  - 上行：规范四段 → `status=ok`、缺段 → `status=degraded` 且带 `report_path/raw_text`、空返回 → `DELEGATION_EMPTY_RESULT`、业务失败 → `outcome=failure`、通用子 Agent 纯文本 → `【文本结果】`；
  - 运行时：`Command` 内 `ToolMessage` 被替换且其他 state update 保留、子 Agent 异常被包装、分析/订单超时语义、`CancelledError` 原样传播；
  - 文档：三处提示词源仍包含四段，AGENTS.md 指向 `DelegationContractMiddleware`。
- 运行时链路（3 passed）：`tests/test_delegation_runtime.py`
  - 通过真实 `ToolNode` 执行 `task`，覆盖 deepagents 返回 `Command` 的形态；
  - `ToolNode` 内子 Agent 异常被包装为 `status=error`；
  - 静态断言 `main_agent.py` 已注册 `DelegationContractMiddleware` 且包含 `general-purpose`。
- 回归（38 passed）：`tests/test_agent_boundaries.py`（7）、`tests/test_contracts.py`（5）、`tests/test_tool_io.py`（8）、`tests/test_procurement_task_tools.py`（12）、`tests/test_procurement_observability.py`（6）。
- 文档：`README.md`「委派与回传契约」、`src/agent/memory/AGENTS.md`「委派契约的执行边界」已与实现对齐。

已知限制：

- 未在完整 `create_deep_agent` 图（需要 Redis/MCP/OpenSandbox）上做端到端启动验证；当前用 `ToolNode` + `Command` 覆盖运行时返回形态，`main_agent` 注册用静态断言覆盖。环境可用时应补一条真实 agent 集成测试。
- 同步 `wrap_tool_call` 只做异常兜底，不强制超时；生产异步路径（`awrap_tool_call`）才执行 600s/120s 超时。
- 订单子 Agent 的异常一律按 `side_effect_unknown=true` 处理，属于保守策略：可能把“尚未写订单的异常”也提示为需查询，但可避免重复修改。
