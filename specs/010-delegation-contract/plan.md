# 计划：委派内容结构化与失败兜底（010）

- 规格：`specs/010-delegation-contract/spec.md`（已确认 2026-11-04）
- 目标：把 `task` 的下行简报、上行回传收敛为可校验/可降级/可追踪的结构化契约；子 Agent 异常、超时、空返回、未知类型不再打断主回合。
- 已确认决策：Q-1=A（边界结构化）、Q-2=C（JSON 信封 + 状态头/人读提示）、Q-3=分析 600s/订单 120s、Q-4=不自动重试、Q-5=订单写超时 `side_effect_unknown=true` 且不自动重试、Q-6=`raw_text` 2000 字符。

## 1. 现状与关键约束

- 现有 `DelegationContractMiddleware` 只处理 `ToolMessage`；当前 deepagents 的 `task` 成功时返回 `Command(update={"messages": [ToolMessage(...)]})`，因此即使注册也不会处理上行回传。
- 现有中间件未加入 `main_agent` 的 middlewares；README/AGENTS.md 却宣称已生效。
- 现有 `contract.py` 的纯函数（`validate_brief/patch_brief/parse_report`）可复用；测试目前只覆盖纯函数和手工 `ToolMessage`。
- 子 Agent 提示词依赖四段文本，所以下行保持四段文本，不改成 JSON 入参。
- 订单写路径的 `order_guard`、审批、唯一创建入口不能被契约层绕过。

## 2. 方案取舍

### 2.1 下行

**选定：边界结构化。**
- 模型仍传 `description` 四段文本（兼容现有提示词）。
- 中间件解析为 `BriefCheck`，缺【任务目标】/【需求正文】直接拦回；缺【用户偏好】/【输出要求】用可信运行时上下文补齐。
- 额外支持“JSON 字符串简报”作为前向兼容输入，解析后渲染为规范四段文本再交给子 Agent。
- 未确认的子 Agent 类型在派发前拦截，返回可用类型列表。

备选（不采用）：替换 `task` 为带 schema 的委派工具。结构更强，但需要改主/子 Agent 全部提示词与调用方式，超出本次范围。

### 2.2 上行

**选定：版本化 JSON 信封 + 状态头 + 人读提示。**
- 信封字段：`contract_version`、`status`、`agent`、`outcome`、`code`、`message`、`retryable`、`side_effect_unknown`、`missing`、`report_path`、`fields`、`raw_text`、`duration_ms`。
- `status`：`ok` / `degraded` / `error` / `timeout`。
- `fields` 使用规范中文段落名（【报告路径】等），保持与现有子 Agent 输出契约一致。
- 成功时只放结构化字段；降级/失败时保留截断后的 `raw_text` 和可提取的 `report_path`。
- 父 Agent 看到的是「一行状态头 + JSON 代码块 + 必要提示」；`status!=ok` 时提示不要直接把原文当结论。

### 2.3 失败分类

| 场景 | status | code | retryable | 备注 |
| --- | --- | --- | --- | --- |
| 缺必需段落 | error | `DELEGATION_BRIEF_INVALID` | true | 不派发，模型补全后可重试 |
| 未知子 Agent | error | `DELEGATION_AGENT_UNKNOWN` | true | 返回 `available_agents` |
| 空返回 | error | `DELEGATION_EMPTY_RESULT` | true | 不当成功 |
| 超时（分析） | timeout | `DELEGATION_TIMEOUT` | true | 600s |
| 超时（订单） | timeout | `DELEGATION_TIMEOUT` | false | 120s，`side_effect_unknown=true` |
| 限流 | error | `DELEGATION_RATE_LIMITED` | true | 类名/消息含 rate limit/429 |
| 传输/连接 | error | `DELEGATION_TRANSPORT_ERROR` | true | 类名含 connection/transport |
| 递归超限 | error | `DELEGATION_RECURSION_LIMIT` | false | 建议改派/拆任务 |
| 其他异常 | error | `DELEGATION_FAILED` | false | 不泄漏堆栈 |
| 契约缺段 | degraded | `DELEGATION_REPORT_INCOMPLETE` | true | 提示先读文件产物 |

- 订单子 Agent 的异常/超时一律 `side_effect_unknown=true`、`retryable=false`，父 Agent 必须先查询订单，不能自动重派写操作。
- 中断（`GraphInterrupt`）与取消（`CancelledError`）原样传播，不包装。
- 契约层不自动重试任何子 Agent 调用；`retryable` 只作为父模型的决策输入。

## 3. 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/agent/subagents/contract.py` | 新增 `BriefValidationError`、`prepare_brief`（含 JSON 兼容）、`DelegationResult`、`build_result_from_text`、`build_error_result`、`classify_exception`、`render_result`；保留现有 `parse_report` 等纯函数 |
| `src/agent/middlewares/delegation_contract.py` | 重写 `_prepare`/`_finalize`；支持 `Command` 内 `ToolMessage` 替换；异步超时；异常兜底；已知子 Agent 校验；结构化日志 |
| `src/agent/main_agent.py` | 导入并把 `DelegationContractMiddleware` 加入主 Agent middlewares；传入已加载子 Agent 名称与超时配置 |
| `tests/test_delegation_contract.py` | 更新现有断言到 JSON 信封；补下行/上行失败用例 |
| `tests/test_delegation_runtime.py`（新增） | 用真实 `ToolNode`/`Command` 链路验证运行时接入、超时、异常、空返回 |
| `README.md` | 把“委派与回传契约”更新为实际行为（结构化信封、失败分类、超时、不自动重试写操作） |
| `src/agent/memory/AGENTS.md` | 同步委派执行边界与失败语义 |

## 4. 接口/契约变更

### 4.1 父 Agent 收到的 ToolMessage 内容（示例）

```text
【委派结果】status=degraded agent=procurement-analyst retryable=true duration_ms=8123
```json
{
  "contract_version": "1.0",
  "status": "degraded",
  "agent": "procurement-analyst",
  "outcome": null,
  "code": "DELEGATION_REPORT_INCOMPLETE",
  "message": "子 Agent 未按回传契约返回，以下原文不保证结构完整。",
  "retryable": true,
  "side_effect_unknown": false,
  "missing": ["【采购建议】"],
  "report_path": "/analysis/report_1.md",
  "fields": {"【报告路径】": "/analysis/report_1.md"},
  "raw_text": "……（截断到 2000 字符）",
  "duration_ms": 8123
}
```
请先 `read_file` 读取 `/analysis/report_1.md`，再向用户汇报；不要直接把 raw_text 当结论。
```

### 4.2 行为变更

- `task` 成功但子 Agent 文本为空：由“空 ToolMessage 当成功”变为 `status=error`。
- 子 Agent 异常/超时：由“冒泡打断主回合”变为可消费的 `status=error/timeout` ToolMessage。
- 订单写类异常/超时：父 Agent 收到 `side_effect_unknown=true`，不会被诱导自动重试。
- 已知子 Agent 校验在派发前完成，未知类型不再进入框架的纯文本提示分支。

## 5. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1/AC-2/AC-3/AC-4/AC-5 | `tests/test_delegation_contract.py` 下行用例；`prepare_brief` 单测 |
| AC-6/AC-7/AC-8/AC-11 | `parse_report` + `build_result_from_text` 单测；JSON 字段断言 |
| AC-9/AC-10 | 中间件 handler 抛异常/超时；断言状态、code、retryable、side_effect_unknown |
| AC-12/AC-13 | handler 抛 `GraphInterrupt`/`CancelledError`，断言原样传播 |
| AC-14 | `tests/test_delegation_runtime.py` 用 `ToolNode` + 真实 `Command` 返回验证；并断言 main_agent 中间件列表包含契约中间件 |
| AC-15 | 现有 `tests/test_agent_boundaries.py` 回归 |
| AC-16 | 订单超时用例断言 `retryable=false` + `side_effect_unknown=true`，并断言 handler 只被调用一次 |
| AC-17 | 用 `_Logger` 捕获 `delegation.result`，断言不含简报正文 |
| AC-18 | README/AGENTS 文本断言 |

回归命令：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_delegation_contract.py tests/test_delegation_runtime.py tests/test_agent_boundaries.py -q
```

## 6. 风险与回滚

| 风险 | 处理 |
| --- | --- |
| 中间件顺序导致 `wrap_tool_call` 不生效 | 在 main_agent 注册并加运行时集成测试；必要时调整到 limits 之前 |
| `Command.update` 结构变化/无 `messages` | `_finalize` 只替换匹配 `tool_call_id` 的 `ToolMessage`，其他 update 原样保留；无匹配则透传 |
| 超时取消子 Agent 可能留下半成品/副作用 | 订单类标记 `side_effect_unknown=true`，不自动重试；分析类可重试 |
| 同步路径无法可靠超时 | 同步只做异常兜底；超时在异步路径强制，plan/tasks 中写明限制 |
| 父 Agent 不习惯 JSON 信封 | 状态头 + 人读提示；保留 `fields` 中文段落名，兼容现有阅读方式 |
| 现有测试断言文本 | 更新为解析 JSON；保留 `parse_report` 兼容层 |

回滚：单独 revert 中间件注册即可恢复原行为；`contract.py` 新增函数为增量，不影响旧调用方。

## 7. 实施顺序

1. contract 层（简报/信封/分类）
2. middleware 层（下行为主，再 Command/超时/异常）
3. main_agent 注册
4. 单测 + 运行时集成测试
5. 文档与验收证据
