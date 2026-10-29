# 任务清单：自助注册、账号校验与基于角色的权限控制

- 对应规格：`specs/002-registration-rbac/spec.md`
- 对应方案：`specs/002-registration-rbac/plan.md`
- 状态：已完成（2026-10-29）

## 阶段一：账号与校验

- [x] T1 用户表扩展与仓储能力
  - 文件：`src/api_view/auth/users.py`
  - 内容：display_name、must_change_password、last_login_at；幂等 ALTER；列表/搜索/角色/状态/密码更新；最后 admin 计数
- [x] T2 注册校验、密码策略与注册接口
  - 文件：`src/api_view/auth/validation.py`、`src/api_view/auth/router.py`、`src/api_view/auth/sessions.py`
  - 内容：username/display_name/password/confirm 校验；user_id 生成；IP 注册限流；自动登录
- [x] T3 登录状态、改密、强制改密、退出所有设备
  - 文件：`src/api_view/auth/router.py`、`dependencies.py`、`sessions.py`
  - 内容：DB 回查用户；disabled 401；must_change 门禁；会话索引与 revoke_all
- [x] T4 注册/登录/改密/会话测试
  - 文件：`tests/test_auth_registration.py`、`tests/test_auth_password_change.py`、`tests/fakes.py`

## 阶段二：角色与管理员

- [x] T5 角色申请与管理员审批
  - 文件：`src/api_view/auth/users.py`、`src/api_view/auth/admin_router.py`
  - 内容：app_role_request 表、申请/审批/拒绝、重复 pending、禁止自审批
- [x] T6 管理员用户管理接口
  - 文件：`src/api_view/auth/admin_router.py`
  - 内容：列表/搜索/启停/改角色/重置临时密码；自我修改与最后 admin 保护
- [x] T7 权限依赖与即时生效
  - 文件：`src/api_view/auth/permissions.py`、`web_main.py`
  - 内容：`require_roles`、`require_password_ready`；注册 admin 路由；会话角色以 DB 为准
- [x] T8 角色/管理员测试
  - 文件：`tests/test_auth_admin.py`、`tests/test_auth_roles.py`

## 阶段三：权限收口

- [x] T9 采购 API 改用会话身份
  - 文件：`src/api_view/api/procurement.py`
  - 内容：删除本地 `get_current_user`/`X-User-Id`/`X-Roles`/`guoyaxing`；角色来自 DB；执行记录 owner/admin
- [x] T10 历史会话隔离
  - 文件：`src/api_view/api/history.py`
  - 内容：列表按归属过滤；消息仅 owner；删除/改标题 owner/admin；admin 不可读正文
- [x] T11 前端采购/历史凭据
  - 文件：`frontend/src/api/procurement.js`、`frontend/src/api/history.js`
  - 内容：删除硬编码头，same-origin Cookie，401 通知
- [x] T12 权限测试
  - 文件：`tests/test_procurement_auth.py`、`tests/test_history_isolation.py`

## 阶段四：前端

- [x] T13 登录/注册页
  - 文件：`frontend/src/components/LoginView.vue`、`frontend/src/api/auth.js`
  - 内容：双 Tab、前端校验、注册自动登录、策略提示
- [x] T14 强制改密与用户菜单
  - 文件：`frontend/src/components/ChangePassword.vue`、`AppHeader.vue`、`App.vue`
  - 内容：改密、退出所有设备、admin 入口、must_change 门禁
- [x] T15 管理员面板
  - 文件：`frontend/src/components/AdminPanel.vue`、`frontend/src/api/auth.js`
  - 内容：用户列表/搜索/启停/改角色/重置密码；角色申请审批

## 阶段五：验收

- [x] T16 全量回归、文档与证据
  - 文件：`README.md`、`AGENTS.md`、`specs/002-registration-rbac/spec.md`
  - 内容：注册/角色/管理员说明、临时密码策略、权限矩阵、AC 证据
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`；`cd frontend && npm run build`

## 完成定义

- [x] AC-1~AC-12 有测试或可复现证据
- [x] 全量 pytest 与前端构建通过
- [x] 后端无 `X-User-Id`/`X-Roles`/`guoyaxing` 生产回退
- [x] 最后 admin 保护、强制改密、角色即时生效可验证
- [x] 日志无密码/哈希/token
