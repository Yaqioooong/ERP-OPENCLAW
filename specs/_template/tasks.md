# 任务清单：<需求名称>

- 对应规格：`specs/NNN-<slug>/spec.md`
- 对应方案：`specs/NNN-<slug>/plan.md`
- 状态：未开始 / 进行中 / 已完成

> 规则：一条任务一次提交能完成；先契约/测试，后实现；每完成一条就勾选并记录验证结果。
> 发现规格问题先改 `spec.md`，不要用代码“顺手”改变行为。

## 阶段一：契约与测试

- [ ] T1 编写/更新测试用例，覆盖 AC-1、AC-2
  - 文件：`tests/test_xxx.py`
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_xxx.py -q`（预期先失败）
- [ ] T2 定义接口/数据结构（MCP 工具签名、Pydantic 模型、路由 schema）
  - 文件：
  - 验证：导入无误、schema 单测通过

## 阶段二：后端实现

- [ ] T3 实现领域逻辑
  - 文件：
  - 验证：
- [ ] T4 接入 API / MCP 工具
  - 文件：
  - 验证：`curl` 或测试客户端调用成功
- [ ] T5 权限、参数校验与错误处理
  - 文件：
  - 验证：异常分支测试通过

## 阶段三：前端/Agent 集成

- [ ] T6 前端交互与状态展示
  - 文件：
  - 验证：本地手工走通主路径
- [ ] T7 Agent 提示词/工具注册（如涉及）
  - 文件：
  - 验证：对话触发工具并返回预期结果

## 阶段四：验收

- [ ] T8 逐条核对 `spec.md` 验收标准，回填验收证据
- [ ] T9 全量回归
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -q`
- [ ] T10 更新文档（README / API / 技能说明）
- [ ] T11 代码检查
  - 验证：`.venv/bin/ruff check src tests`、`.venv/bin/mypy src`（如已配置）

## 完成定义（DoD）

- [ ] 所有验收标准有对应测试或可复现的验证记录
- [ ] 全量测试通过，无新增告警
- [ ] 接口契约与文档一致
- [ ] 回滚方式明确且验证过
- [ ] 提交信息包含规格编号
