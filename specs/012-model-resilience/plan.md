# 计划：模型调用超时、重试与降级（012）

- 规格：`specs/012-model-resilience/spec.md`（已确认 2026-11-05）
- 目标：模型调用有 timeout、有限退避重试、跨模型 fallback；不可重试错误不制造额外延迟。

## 1. 方案

### 1.1 模型级配置

`src/agent/config.py`：

- `MODEL_REQUEST_TIMEOUT_SECONDS`（默认 180）
- `MODEL_RETRY_MAX_ATTEMPTS`（默认 2，即最多 3 次尝试）
- `MODEL_RETRY_INITIAL_DELAY_SECONDS`（默认 1.0）
- `MODEL_RETRY_MAX_DELAY_SECONDS`（默认 8.0）

模型实例：

| 模型 | timeout | SDK max_retries | 说明 |
| --- | --- | --- | --- |
| `MAIN_MODEL` | 180s | 0 | 由 `ModelRetryMiddleware` 统一重试 |
| `SUMMARY_MODEL` | 180s | 1 | 直调路径保留一次 SDK 重试 |
| `FALLBACK_MODEL` | 180s | 0 | 备用模型不额外重试 |
| `SUMMARY_MODEL_FALLBACK` | — | — | `SUMMARY_MODEL.with_fallbacks([FALLBACK_MODEL])`，供直调路径使用 |

`FALLBACK_MODEL` 增加 `max_tokens=8192`，避免备用模型输出被默认值截断。

### 1.2 中间件

新增 `src/agent/model_resilience.py`：

- `is_retryable_model_error(exc)`：`ModelError.is_retryable`、状态码 408/409/425/429/5xx、
  类名/消息含 timeout/ratelimit/connection/overloaded 等返回 True；400/401/403 等返回 False。
- `build_model_fallback_middleware()`：`ModelFallbackMiddleware(FALLBACK_MODEL)`。
- `build_model_retry_middleware()`：`ModelRetryMiddleware(max_retries=2, retry_on=is_retryable_model_error, on_failure="error", backoff_factor=2.0, initial_delay=1.0, max_delay=8.0, jitter=True)`。

顺序：**fallback 在外、retry 在内**，即主模型先按策略重试，重试耗尽后再切 fallback；
fallback 的调用也会经过 retry（因为 retry 是内层 handler）。

注册：

- `src/agent/main_agent.py` 主 Agent 中间件栈。
- `src/agent/middleware_config.py` 的 `create_analyst_middleware` / `create_order_middleware`。

### 1.3 摘要直调路径

将 `SUMMARY_MODEL_FALLBACK` 用于：

- `src/agent/main_agent.py` 的 `MemoryCandidateGenerator`
- `src/agent/procurement/dependencies.py` 的 `LlmReportNarrator`、`LLMRequestParser`

`SummarizationMiddleware` 内部仍使用 `SUMMARY_MODEL`（它需要 `BaseChatModel` 的
`_llm_type/profile`，不能直接传 `RunnableWithFallbacks`）；其失败由子 Agent
`task` 边界（010）兜底。

## 2. 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/agent/config.py` | 超时/重试环境变量；模型 timeout/max_retries；`FALLBACK_MODEL` 补 max_tokens；`SUMMARY_MODEL_FALLBACK` |
| `src/agent/model_resilience.py`（新增） | 可重试异常分类、fallback/retry 中间件工厂 |
| `src/agent/main_agent.py` | 注册 fallback/retry；`MemoryCandidateGenerator` 使用 `SUMMARY_MODEL_FALLBACK` |
| `src/agent/middleware_config.py` | 两个子 Agent 注册 fallback/retry |
| `src/agent/procurement/dependencies.py` | 解析器/报告解释器使用 `SUMMARY_MODEL_FALLBACK` |
| `tests/test_model_resilience.py`（新增） | 重试/fallback/不可重试/中断传播/接线断言 |
| `README.md` | 模型降级说明 |

## 3. 风险

| 风险 | 处理 |
| --- | --- |
| fallback 模型不支持工具调用 | `FALLBACK_MODEL` profile 声明 `tool_calling=True`；接入测试断言可 `bind_tools` |
| 双重重试导致长延迟 | 主模型 SDK `max_retries=0`；中间件最多 3 次；fallback 自身 0 次 SDK 重试 |
| fallback 上下文更小（128k） | 当前只做失败切换，不做上下文裁剪；超限仍失败，后续 spec |
| 摘要中间件无法用 Runnable 包装 | 保留 `SUMMARY_MODEL`；失败由 010 `task` 边界兜底 |
| fallback 掩盖真实业务错误 | 只对模型调用异常生效；400/401 等不做无意义重试，但仍尝试 fallback 一次 |
| 日志泄漏 prompt/响应 | 只记录异常类型、尝试次数、是否 fallback，不记录正文 |

## 4. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1/AC-10 | 静态断言 config 含 timeout/env；模型实例 timeout 参数 |
| AC-2/AC-3 | `is_retryable_model_error` 分类；`ModelRetryMiddleware` 重试次数/不重试 |
| AC-4/AC-5 | `ModelFallbackMiddleware` 主失败→fallback 成功；两者都失败→抛出 |
| AC-6 | `GraphInterrupt` 不被 fallback/retry 吞 |
| AC-7/AC-8 | 静态断言 main/middleware_config 注册 |
| AC-9 | 静态断言摘要直调路径使用 `SUMMARY_MODEL_FALLBACK` |
| AC-10 | 日志字段断言（无 prompt） |

## 5. 实施顺序

1. config 与 model_resilience 工厂
2. 主 Agent / 子 Agent 注册
3. 摘要直调路径接线
4. 单测与文档
