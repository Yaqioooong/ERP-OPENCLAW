# 任务：工具调用异常与超时兜底（011）

- 规格：`specs/011-tool-resilience/spec.md`
- 计划：`specs/011-tool-resilience/plan.md`

## 任务清单

- [x] 1. 新增 `src/agent/middlewares/tool_resilience.py`：策略表、超时、异常分类、错误信封、日志。验证：`test_policy_*`、`test_read_*`。
- [x] 2. 实现异步 `awrap_tool_call`：读工具有限重试、写工具单次、`GraphInterrupt`/`CancelledError` 透传。验证：超时/重试/中断/取消单测。
- [x] 3. 实现同步 `wrap_tool_call`：异常兜底、不强制超时。验证：同步异常单测。
- [x] 4. 在 `src/agent/middleware_config.py` 为分析和订单子 Agent 注册中间件。验证：静态断言。
- [x] 5. 在 `src/agent/main_agent.py` 为主 Agent 注册中间件并跳过 `task`。验证：静态断言。
- [x] 6. 新增 `tests/test_tool_resilience.py`，覆盖 AC-1～AC-14。验证：目标测试通过。
- [x] 7. 增加真实 `ToolNode` 用例：工具超时/异常返回 error ToolMessage，不抛出。验证：集成测试通过。
- [x] 8. 更新 README / AGENTS.md 的工具失败语义（简短）。验证：文档断言。
- [x] 9. 跑目标回归并回填 spec 第 7 节验收证据。验证：测试输出 + 证据。
