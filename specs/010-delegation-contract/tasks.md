# 任务：委派内容结构化与失败兜底（010）

- 规格：`specs/010-delegation-contract/spec.md`
- 计划：`specs/010-delegation-contract/plan.md`
- 约定：每条任务一次提交；完成后勾选并在括号内写验证方式。

## 任务清单

- [x] 1. 在 `src/agent/subagents/contract.py` 增加委派版本常量、错误码、`BriefValidationError`、`prepare_brief`（含四段文本与 JSON 字符串兼容）。验证：`test_prepare_brief_*` 单测。
- [x] 2. 增加 `DelegationResult`、`build_result_from_text`、`build_error_result`、`classify_exception`、`render_result`；保留现有 `parse_report` 兼容。验证：纯函数单测。
- [x] 3. 重写 `DelegationContractMiddleware._prepare`：KNOWN 子 Agent 校验、缺必需段拦回、可补齐段补齐、未知类型返回 `available_agents`。验证：下行单测。
- [x] 4. 重写 `DelegationContractMiddleware._finalize`：支持 `ToolMessage` 与 `Command(update["messages"])`，只替换匹配 `tool_call_id` 的消息，保留其他 state update。验证：Command 单测 + 运行时 ToolNode 用例。
- [x] 5. 在中间件加入异步超时（分析 600s/订单 120s，可配置）与异常分类兜底；`GraphInterrupt`/`CancelledError` 原样传播；同步路径只做异常兜底。验证：超时/异常/中断/取消单测。
- [x] 6. 在 `src/agent/main_agent.py` 注册 `DelegationContractMiddleware`，传入已加载子 Agent 名称与超时配置。验证：断言 main middleware 列表包含它。
- [x] 7. 更新 `tests/test_delegation_contract.py` 现有断言到 JSON 信封，并补 AC-1/2/3/4/5/6/7/8/11 用例。验证：目标测试通过。
- [x] 8. 新增 `tests/test_delegation_runtime.py`：真实 `ToolNode` + `Command` 返回、超时、异常、空返回、订单 `side_effect_unknown`。验证：目标测试通过。
- [x] 9. 更新 `README.md` 的“委派与回传契约”：结构化信封、失败分类、超时、不自动重试写操作；删除“仅文本解析”的旧描述。验证：文档断言。
- [x] 10. 更新 `src/agent/memory/AGENTS.md` 的委派执行边界，说明 `status=error/degraded` 的父 Agent 处理方式。验证：文档断言。
- [x] 11. 跑目标回归：`tests/test_delegation_contract.py`、`tests/test_delegation_runtime.py`、`tests/test_agent_boundaries.py`。验证：全部通过。
- [x] 12. 回填 spec 第 7 节验收证据（测试用例名、输出摘要、已知限制）。验证：证据与 AC 对齐。
