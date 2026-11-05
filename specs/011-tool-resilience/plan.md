# 计划：工具调用异常与超时兜底（011）

- 规格：`specs/011-tool-resilience/spec.md`（已确认 2026-11-04）
- 目标：工具异常/超时 → 结构化 `ToolMessage`，不打断 Agent 回合；读工具有限重试，写工具绝不自动重试。

## 1. 方案

新增 `ToolResilienceMiddleware`，在 `wrap_tool_call` / `awrap_tool_call` 中：

1. 按工具名选择超时与读写策略。
2. 异步路径用 `asyncio.wait_for`；超时/异常按策略重试。
3. 读工具：最多 2 次尝试（1 次重试），仅对超时/连接/429/传输类错误重试；退避 0.25s。
4. 写工具：只尝试 1 次；超时/异常返回 `side_effect_unknown=true`、`retryable=false`。
5. `GraphInterrupt` / `CancelledError` 原样传播；`task` 跳过（由 010 处理）。
6. 失败结果渲染为 `ToolMessage(status="error")`，内容为状态头 + JSON 信封 + 人读提示。

`ToolNode` 默认只兜 `ToolInvocationError`；本中间件在工具边界捕获其余异常，模型下一步看到 tool error，可以换查询/缩小范围/告知用户。

## 2. 策略表

| 工具 | 超时 | 读写 | 重试 |
| --- | --- | --- | --- |
| `execute` | 300s | 写 | 否 |
| `generate_visualization` / `compact_conversation` / `assign_skill` / `download_sandbox_file` | 120s | 读/写分别 | 读可重试，写不重试 |
| `order_create` / `order_update` | 120s | 写 | 否 |
| `start_procurement_task` | 60s | 写 | 否 |
| 默认（ERP 查询、web_search、read_file 等） | 45s | 读 | 是 |
| `task` | — | — | 跳过 |

错误码：`TOOL_TIMEOUT`、`TOOL_RATE_LIMITED`、`TOOL_UNAVAILABLE`、`TOOL_FAILED`。
信封字段：`status`、`tool`、`code`、`message`、`retryable`、`side_effect_unknown`、`attempts`、`duration_ms`。

## 3. 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/agent/middlewares/tool_resilience.py`（新增） | 中间件、策略表、异常分类、错误信封渲染、日志 |
| `src/agent/middleware_config.py` | 分析和订单子 Agent 中间件栈加入 `ToolResilienceMiddleware` |
| `src/agent/main_agent.py` | 主 Agent 中间件栈加入 `ToolResilienceMiddleware(skip_tools=("task",))` |
| `tests/test_tool_resilience.py`（新增） | 超时/重试/写工具/中断/取消/ToolNode 集成 |
| `README.md` / `AGENTS.md` | 补充工具失败语义（可选，简短） |

## 4. 风险

| 风险 | 处理 |
| --- | --- |
| 读工具重试放大延迟 | 最多 1 次重试；默认 45s，最坏约 90s；可用参数调小 |
| 写工具超时但远端已执行 | `side_effect_unknown=true`，不自动重试，提示先查询/确认 |
| `execute` 长任务被误杀 | 单独 300s；仍超时则按写工具处理 |
| 同步路径无法超时 | 只做异常兜底；异步路径强制超时，文档写明 |
| 与 010 重复包装 `task` | 主 Agent 显式 `skip_tools=("task",)` |
| 中断被吞 | 显式捕获 `GraphInterrupt`/`CancelledError` 并重新抛出 |

## 5. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1/AC-2/AC-3 | 读工具超时/瞬时异常/非瞬时异常；断言尝试次数、code、retryable |
| AC-4/AC-5 | 写工具超时/异常；断言只调用一次、`side_effect_unknown`、`retryable=false` |
| AC-6 | 成功结果原样返回 |
| AC-7/AC-8 | `GraphInterrupt`/`CancelledError` 传播 |
| AC-9 | `task` 跳过 |
| AC-10 | 策略表单测 |
| AC-11 | 静态断言 main/subagent middleware 注册 |
| AC-12 | 日志字段断言（不含参数正文） |
| AC-14 | 同步异常兜底 |

回归：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_tool_resilience.py tests/test_delegation_contract.py tests/test_delegation_runtime.py tests/test_agent_boundaries.py -q
```

## 6. 实施顺序

1. 新增中间件与策略/错误信封
2. 注册到主 Agent 和两个子 Agent
3. 单测 + ToolNode 集成
4. 文档与验收证据
