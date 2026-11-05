# 规格：模型调用超时、重试与降级

- 规格编号：`012`
- 状态：已确认（2026-11-05，按推荐默认值实施）
- 提出人：采购 Agent 使用者
- 日期：2026-11-05
- 关联需求：失败降级专项 P0-8；`FALLBACK_MODEL` 未使用

## 1. 背景与目标

主 Agent、采购分析子 Agent、订单子 Agent 都依赖 LLM。当前：

- `MAIN_MODEL` / `SUMMARY_MODEL` 没有显式 `timeout` 和 `max_retries`，依赖 SDK 默认；
- `FALLBACK_MODEL` 已定义但全仓零引用，主模型 429/5xx/超时时没有任何模型级降级；
- 直接调用摘要模型的路径（采购需求解析、报告解释、记忆候选）同样没有 fallback；
- 模型异常会冒泡到 `task`/工作流，轻则整个子任务失败，重则用户看到裸错误。

本次目标：模型调用有明确超时、有限退避重试和跨模型降级；可重试错误先重试，
仍失败再切备用模型；不可重试错误直接暴露为明确失败。

## 2. 用户故事

- 作为采购分析使用者，我希望主模型限流或超时时，系统自动重试或切换备用模型，
  而不是直接回我一段报错。
- 作为订单维护使用者，我希望模型故障时子 Agent 不会静默重试写操作，仍由
  既有 HITL/订单守卫控制副作用。
- 作为排障人员，我希望知道模型调用失败的类型、尝试次数和是否已切换备用模型，
  且日志不包含 prompt/响应正文。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 超时配置 | 模型请求超时 | 调用 | 请求在配置的 timeout 内结束；不无限挂起 |
| AC-2 | 可重试错误 | 429/5xx/连接失败/SDK 标记可重试 | 模型调用 | 按指数退避重试，最多 `MODEL_RETRY_MAX_ATTEMPTS` 次；重试耗尽后进入 fallback |
| AC-3 | 不可重试错误 | 400/401/403/参数错误 | 模型调用 | 不重试，直接进入 fallback 或抛出明确失败 |
| AC-4 | 主模型失败 | 主模型重试耗尽 | 模型调用 | 自动切换到 `FALLBACK_MODEL`；成功则回合继续 |
| AC-5 | 备用模型也失败 | 主模型 + fallback 都失败 | 模型调用 | 抛出最后一次异常；由上层 `task`/工作流/聊天流兜底，不无限循环 |
| AC-6 | 中断/取消 | 模型调用期间发生 `GraphInterrupt` / `CancelledError` | 调用 | 原样传播，不被 fallback/retry 吞掉 |
| AC-7 | 主 Agent 接入 | 构建主 Agent | 模型调用 | 中间件栈包含 fallback 与 retry |
| AC-8 | 子 Agent 接入 | 构建采购分析/订单子 Agent | 模型调用 | 两个子 Agent 都包含 fallback 与 retry |
| AC-9 | 摘要直调路径 | 采购解析、报告解释、记忆候选直接调用摘要模型 | 调用 | 使用带 fallback 的摘要模型；失败不裸抛到用户 |
| AC-10 | 配置可见 | 部署环境 | 启动 | timeout/重试参数可通过环境变量覆盖；日志只记录类型/次数，不记录 prompt/响应正文 |

## 4. 范围

**包含：**

- `MAIN_MODEL` / `SUMMARY_MODEL` / `FALLBACK_MODEL` 的 timeout 与 SDK 重试配置。
- 主 Agent、采购分析子 Agent、订单子 Agent 的模型 fallback + retry 中间件。
- 采购解析、报告解释、记忆候选三条摘要直调路径的 fallback。
- 可重试异常分类、环境变量配置、日志。
- 单测与接线断言。

**不包含：**

- 上下文超限的自动裁剪/重放（后续 spec）。
- 按用户/租户的配额、断路器、熔断、跨进程限流。
- 修改提示词或子 Agent 业务逻辑。
- 为 deepagents 内置 `general-purpose` 子 Agent 注入中间件（其模型调用仍受父级 `task` 兜底）。

## 5. 约束与依赖

- 使用 LangChain 内置 `ModelFallbackMiddleware` / `ModelRetryMiddleware`，不自定义
  破坏 `bind_tools` 的模型包装器。
- fallback 模型必须支持 tool calling；当前 `FALLBACK_MODEL` 的 profile 声明为支持。
- fallback 只处理模型调用异常，不改变订单写路径、HITL、审批和幂等。
- 主 Agent 模型需要 `BaseChatModel` 才能绑定工具，因此 fallback 必须放在中间件层。
- 摘要直调路径只调用 `.ainvoke`，可以使用 `Runnable.with_fallbacks`。
- 环境变量：`MODEL_REQUEST_TIMEOUT_SECONDS`、`MODEL_RETRY_MAX_ATTEMPTS`、
  `MODEL_RETRY_INITIAL_DELAY_SECONDS`、`MODEL_RETRY_MAX_DELAY_SECONDS`。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 默认超时？ | 长生成体验 | 需求方 | 已确认：180s |
| Q-2 | 重试次数？ | 延迟/恢复率 | 需求方 | 已确认：最多 2 次重试（3 次尝试），指数退避，初始 1s，上限 8s |
| Q-3 | 不可重试错误是否也尝试 fallback？ | 能否跨 provider 恢复 | 需求方 | 已确认：是；fallback 只尝试一次，不递归 |
| Q-4 | 摘要模型是否配置 SDK 重试？ | 直调路径恢复 | 需求方 | 已确认：摘要模型保留 1 次 SDK 重试，并额外加 fallback |
| Q-5 | 主模型是否保留 SDK 重试？ | 避免双重重试 | 需求方 | 已确认：主模型 SDK 重试设为 0，由中间件统一控制 |

## 7. 验收证据

2026-11-05 实施证据：

- 单测（7 passed）：`tests/test_model_resilience.py`
  - 可重试分类：TimeoutError/ConnectionError/429/503 → true；ValueError/400/401 → false；
  - 主模型 3 次尝试后仍失败 → 切 fallback 成功；
  - 不可重试错误只调用 1 次，不重试；
  - 主模型 + fallback 都失败 → 抛出最后异常；
  - `GraphInterrupt` / `CancelledError` 不被 fallback 吞掉；
  - 静态接线：主 Agent 注册 fallback/retry、两个子 Agent 都接入、
    摘要直调路径使用 `SUMMARY_MODEL_FALLBACK`。
- 目标回归（65 passed）：`tests/test_model_resilience.py`（7）、
  `tests/test_tool_resilience.py`（12）、`tests/test_delegation_contract.py`（23）、
  `tests/test_delegation_runtime.py`（3）、`tests/test_agent_boundaries.py`（7）、
  `tests/test_contracts.py`（5）、`tests/test_tool_io.py`（8）。
- 文档：`README.md`「模型调用超时与降级」已更新。

已知限制：

- `SummarizationMiddleware` 内部仍直接使用 `SUMMARY_MODEL`（需要 `BaseChatModel` 的
  `_llm_type/profile`），其失败由子 Agent `task` 边界（010）兜底；
- fallback 模型上下文为 128k，小于主模型；未做上下文裁剪/重放；
- fallback 对不可重试错误（400/401）也会尝试一次，因为可能跨 provider 恢复；
- `general-purpose` 内置子 Agent 不走 `extra_middleware`，未注入模型中间件；
- 没有连接真实 provider 做端到端验证；单测用假模型验证中间件组合与分类逻辑。
