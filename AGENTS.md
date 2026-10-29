# 仓库开发约定

## 规格驱动开发（SDD）

- 任何行为变更，先在 `specs/NNN-<slug>/` 下写 `spec.md`，经确认后再写 `plan.md`、`tasks.md`。
- 完整流程、裁剪规则和模板说明见 [specs/README.md](specs/README.md)。
- 规格未确认前不写实现代码；实现中发现规格有偏差，先改 `spec.md` 并重新确认，禁止“代码即规格”。
- 验收以 `spec.md` 中的验收标准为准，完成后回填验收证据。
- 提交信息带规格编号，例如 `feat(001): ...`。

## 身份、权限与记忆

- 用户身份只能来自服务端认证（`get_current_user` → `ProcurementContext`）；禁止新增默认用户回退，也不信任客户端 `X-User-Id`/`X-Roles`。
- 角色与账号状态以 PostgreSQL `app_user` 为准；`get_current_user` 每次请求回查，禁止把会话中的旧角色当授权依据。
- 管理员接口必须使用 `require_roles("admin")`；业务接口使用 `require_password_ready`，强制改密期间不得放行。
- 前端只做入口显隐，不作为权限边界；新接口必须先在后端声明依赖。
- `/memories/` 必须经 `UserMemoryBackend` 读写，禁止直接调用 Store 写用户记忆；命名空间固定 `(user_id,)`。
- 新增或修改按 `thread_id` 访问的接口，必须同时校验会话归属（`ThreadOwnershipStore`）。
- 记忆日志只允许 `user_id/request_id/thread_id/op/path/outcome` 等白名单字段，不得记录偏好正文、供应商名或查询文本。
- 认证接口测试不要依赖真实 IdP；使用 `tests/fakes.py` 的内存 Redis 与依赖覆盖。

## 实现与验证

- Python 3.10+，后端测试统一用 pytest：`PYTHONPATH=src .venv/bin/python -m pytest tests/... -q`。
- 业务数据不猜测：关键字段缺失时走 HITL 中断向用户确认；不要用默认值伪造业务数据。
- 数据库为 PostgreSQL（`PROCUREMENT_DATABASE_URL`），测试库为 `PROCUREMENT_TEST_DATABASE_URL`。
- 新增 MCP 工具放 `src/mcp_server/tools/`，Agent 侧工具放 `src/agent/tools/`，前端在 `frontend/src/`。
- 改动涉及接口时同步更新 README 与 `plan.md` 中的契约表。
