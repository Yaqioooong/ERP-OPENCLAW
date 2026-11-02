# 任务清单：订单写入单一路径（workflow-first）

- 对应规格：`specs/009-single-order-write-path/spec.md`
- 对应方案：`specs/009-single-order-write-path/plan.md`
- 状态：已完成

> 规则：一条任务一次提交能完成；先契约/测试，后实现；每完成一条就勾选并记录验证结果。
> 发现规格问题先改 `spec.md`，不要用代码“顺手”改变行为。

## 阶段一：契约与测试

- [x] T1 新增边界回归测试，覆盖 AC-1、AC-2、AC-3、AC-5、AC-7
  - 文件：`tests/test_agent_boundaries.py`
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_boundaries.py -q`（实现前 4 条失败 → 实现后 7 passed）

## 阶段二：子 Agent 收窄

- [x] T2 移除 `procurement-order` 的 `order_create` 与 `interrupt_on`，改写描述与系统提示词
  - 文件：`src/agent/subagents/configs/procurement_order.yaml`
  - 验证：T1 通过；工具解析无“未能匹配”告警（`order_update`/`order_search_details` 均命中）

## 阶段三：路由与文档

- [x] T3 主 Agent 提示词改为“对话编排者”，明确唯一创建入口
  - 文件：`src/agent/memory/prompts.py`
  - 验证：`test_main_prompt_routes_creation_to_workflow` 通过
- [x] T4 更新 `/AGENTS.md` 的对话路由、任务分配与安全边界
  - 文件：`src/agent/memory/AGENTS.md`
  - 验证：`test_agents_md_safety_rule_points_to_workflow` 通过；全文检索不再出现“所有订单操作（创建/修改）必须经过”
- [x] T5 更新 README 架构与功能描述
  - 文件：`README.md`
  - 验证：新增“订单写入只有一条路径”一节，与 `spec.md` 一致

## 阶段四：验收

- [x] T6 逐条核对验收标准，回填 `spec.md` 第 7 节
- [x] T7 相关回归
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_agent_boundaries.py tests/test_procurement_task_tools.py tests/test_order_tools.py -q` → 20 passed
- [x] T8 全量回归
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/ -q` → 335 passed / 0 failed / 0 skipped（JUnit 统计），退出码 0

## 完成定义（DoD）

- [x] 所有验收标准有对应测试或可复现的验证记录
- [x] 相关测试通过，无新增告警（仅 1 条既有 `model_kwargs` UserWarning）
- [x] 提示词、子 Agent 配置与 README 表述一致
- [x] 回滚方式明确（恢复四处配置/文档）
- [ ] 提交信息包含规格编号（`feat(009): ...`）—— 待提交时补充
