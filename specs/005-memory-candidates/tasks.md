# 任务清单：记忆候选生成与记忆分级

- 对应规格：`specs/005-memory-candidates/spec.md`
- 对应方案：`specs/005-memory-candidates/plan.md`
- 状态：已完成（2026-11-02）

## 阶段一：候选与生成

- [x] T1 MemoryCandidate 模型
  - 文件：`src/agent/memory/candidates.py`
  - 测试：`tests/test_memory_candidates.py`
- [x] T2 跳过策略与 ERP 意图判断
  - 文件：`src/agent/memory/candidate_generator.py`
  - 内容：整句社交匹配、问候+采购不跳过
- [x] T3 显式偏好 LLM 候选
  - 文件：`src/agent/memory/candidate_generator.py`
  - 内容：显式标记触发、白名单/枚举校验、失败降级
- [x] T4 工具结果供应商与否定过滤
  - 文件：`src/agent/memory/candidate_generator.py`
  - 内容：白名单工具解析、置信度、否定词邻近过滤
- [x] T5 生成器测试
  - 文件：`tests/test_memory_candidates.py`
  - 覆盖：纯问候、问候+采购、否定、AI 摘要不采信、候选字段

## 阶段二：分级持久化

- [x] T6 apply_candidates 与候选统计
  - 文件：`src/agent/memory/preference_service.py`
  - 内容：origin/type/confidence 白名单、去重、统计、推断不覆盖显式
  - 测试：`tests/test_preference_service.py`
- [x] T7 显式工具改用 explicit 候选
  - 文件：`src/agent/tools/preference_tools.py`
  - 内容：tool_call_id source、saved 结果不变

## 阶段三：接线与回归

- [x] T8 中间件/主 Agent 切换
  - 文件：`src/agent/middlewares/memory_update.py`、`src/agent/main_agent.py`
  - 内容：删除旧提取/跳过；generator + apply_candidates
- [x] T9 文档与提示词
  - 文件：`src/agent/memory/AGENTS.md`、`prompts.py`
- [x] T10 全量回归与 005 证据
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`

## 完成定义

- [x] AC-1~AC-9 有测试
- [x] 纯问候无候选；问候+采购不跳过
- [x] 推断只进 recent，显式不被覆盖
- [x] 供应商仅来自工具结果/否定过滤
- [x] 全量测试通过
