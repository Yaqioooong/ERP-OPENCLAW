# 任务：模型调用超时、重试与降级（012）

- 规格：`specs/012-model-resilience/spec.md`
- 计划：`specs/012-model-resilience/plan.md`

## 任务清单

- [x] 1. 在 `src/agent/config.py` 增加 timeout/重试环境变量，给三个模型配置 timeout/max_retries，`FALLBACK_MODEL` 补 max_tokens，新增 `SUMMARY_MODEL_FALLBACK`。验证：静态断言 + 模型实例参数。
- [x] 2. 新增 `src/agent/model_resilience.py`：`is_retryable_model_error`、fallback/retry 工厂。验证：分类单测。
- [x] 3. 主 Agent 注册 fallback/retry，并让 `MemoryCandidateGenerator` 使用 `SUMMARY_MODEL_FALLBACK`。验证：静态断言。
- [x] 4. 两个采购子 Agent 注册 fallback/retry。验证：静态断言。
- [x] 5. `dependencies.py` 的解析器/报告解释器使用 `SUMMARY_MODEL_FALLBACK`。验证：静态断言。
- [x] 6. 新增 `tests/test_model_resilience.py`，覆盖重试/fallback/不可重试/中断传播/接线。验证：目标测试通过。
- [x] 7. 更新 README 的模型降级说明。验证：文档断言。
- [x] 8. 跑目标回归并回填 spec 第 7 节验收证据。验证：测试输出 + 证据。
