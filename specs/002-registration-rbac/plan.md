# 技术方案：自助注册、账号校验与基于角色的权限控制

- 对应规格：`specs/002-registration-rbac/spec.md`
- 状态：已实现（2026-10-29）
- 作者：待填
- 日期：2026-10-29

## 1. 总体设计

```text
注册/登录/改密
  → auth router（校验、限流、审计）
  → PostgreSQL app_user / app_role_request（事实源）
  → Redis 会话 + 用户会话索引
  → get_current_user（每次请求回查用户，角色/状态即时生效）
  → require_password_ready / require_roles
  → 聊天、采购、历史、admin 接口
```

关键原则：

- PostgreSQL 是用户状态与角色的唯一事实源；会话只保存 `user_id`，每次请求回查，禁用/角色变更立即生效。
- 前端只负责体验与入口显隐；所有权限判定在后端依赖/服务层完成。
- 密码、哈希、token、Cookie 不进入日志；临时密码只在重置响应中返回一次。
- 注册开放、立即生效；角色升级才需要管理员审批。

## 2. 数据模型

### 2.1 app_user 扩展（幂等迁移）

```sql
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;
```

最终字段：`user_id, username, display_name, password_hash, roles TEXT[], is_active, must_change_password, last_login_at, created_at, updated_at, password_changed_at`。

### 2.2 app_role_request

```sql
CREATE TABLE IF NOT EXISTS app_role_request (
    request_id     TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    requested_role TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,              -- pending / approved / rejected
    reviewed_by    TEXT REFERENCES app_user(user_id),
    review_comment TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL,
    reviewed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_role_request_status
    ON app_role_request(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_role_request_one_pending
    ON app_role_request(user_id, requested_role)
    WHERE status = 'pending';
```

### 2.3 Redis

| Key | 内容 | TTL |
| --- | --- | --- |
| `erp:auth:session:<sha256(token)>` | `{"user_id"}`（兼容旧字段） | 8h |
| `erp:auth:user_sessions:<user_id>` | 该用户会话 key 的集合 | 随会话 |
| `erp:auth:register:<ip>` | 注册次数 | 1h |
| `erp:auth:fail:<username>:<ip>` | 登录失败次数 | 5min |

会话 `resolve` 只返回 `user_id`；`get_current_user` 回查 `app_user`，不存在/禁用则吊销会话并 401。

## 3. 校验规则

| 字段 | 规则 |
| --- | --- |
| username | 3–32 字符，`^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$`，大小写不敏感唯一；去除首尾空格 |
| display_name | 1–64 字符，去除首尾空格，禁止控制字符 |
| password | 8–128 字符，至少一个字母 + 一个数字，不得等于用户名或包含用户名（大小写不敏感）；拒绝常见弱密码 |
| confirm_password | 必须与 password 完全一致 |
| user_id | 服务端生成 `u_` + 12 位十六进制；冲突重试 3 次 |
| role | 只允许 `requester/approver/admin`；注册固定 `requester`；申请只允许 `approver` |

错误返回：`422 {"detail": {"code": "validation_error", "field_errors": {"password": "..."} }}`；重复用户名 `409 {"detail":{"code":"username_taken"}}`；限流 `429`。

## 4. 接口契约

### 4.1 公开认证

| 方法 | 路径 | 请求 | 成功 |
| --- | --- | --- | --- |
| GET | `/api/auth/policy` | - | 注册开关、用户名/密码规则、角色说明 |
| POST | `/api/auth/register` | `{username, display_name, password, confirm_password}` | `201 {user}` + Set-Cookie，默认 requester，自动登录 |
| POST | `/api/auth/login` | `{username, password}` | `200 {user, must_change_password}` + Cookie |
| POST | `/api/auth/logout` | - | 204 |

`user` 公共字段：`user_id, username, display_name, roles, is_active, must_change_password, last_login_at`。

### 4.2 已登录自助

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/auth/me` | 当前用户（允许 must_change_password） |
| POST | `/api/auth/change-password` | `{current_password, new_password, confirm_password}`；成功后吊销其他会话并重发 Cookie |
| POST | `/api/auth/logout-all` | 吊销该用户全部会话并清 Cookie |
| POST | `/api/auth/role-requests` | `{role: "approver", reason}`；仅 requester 可申请 |
| GET | `/api/auth/role-requests/mine` | 我的申请记录 |

### 4.3 管理员

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/admin/users` | `query, is_active, limit, offset`；返回分页用户 |
| PATCH | `/api/admin/users/{user_id}` | `{is_active?, display_name?, roles?}`；禁止改自己角色/状态；禁止移除最后一个 admin |
| POST | `/api/admin/users/{user_id}/reset-password` | 返回一次性 `temporary_password`，置 `must_change_password=true` 并吊销全部会话 |
| GET | `/api/admin/role-requests` | `status=pending` 列表 |
| POST | `/api/admin/role-requests/{id}/approve` | 批准并追加角色；不能审批自己的申请 |
| POST | `/api/admin/role-requests/{id}/reject` | `{comment}` |

全部 admin 接口要求 `require_roles("admin")` 且已过强制改密门禁。

## 5. 权限与门禁

```python
get_current_user()            # 会话 → DB 回查 → active 用户
require_password_ready(user)  # must_change_password=True → 403 password_change_required
require_roles(*roles)         # 角色不匹配 → 403
```

| 模块 | 依赖/规则 |
| --- | --- |
| auth：me/change-password/logout/logout-all | `get_current_user` |
| 聊天（stream/resume/state/history） | `require_password_ready` + 会话归属（保持 001） |
| 历史列表 | `require_password_ready`；只返回自己的 thread；admin 可看元数据 |
| 历史消息 | 仅归属者；admin 不可读正文 |
| 历史删除/改标题 | 归属者或 admin |
| 采购创建 | `require_password_ready`，任何 active 角色 |
| 采购查看/事件 | 归属者；approver/admin 可看全部 |
| 采购恢复 | 沿用 task_runner 的 owner/approver 状态校验，角色来自 DB |
| 采购执行记录 | 归属者或 admin |
| admin 接口 | `require_roles("admin")` |

`get_current_user` 改造后，001 的会话中 `roles` 仅作兼容展示，不再作为授权依据。

## 6. 管理员与引导

- CLI `scripts/manage_auth_users.py create --roles admin` 创建首个管理员；不提供环境变量默认管理员。
- 管理员不能在 `PATCH` 中修改自己的 `roles`/`is_active`；最后一个 active admin 不可禁用/降级。
- 临时密码：`Tmp` + 随机字母/数字组合，满足密码策略；只在响应中出现一次；管理员界面提示线下交付。
- 注册不做管理员审批；角色升级审批只针对 `approver`。

## 7. 前端改动

| 文件 | 改动 |
| --- | --- |
| `frontend/src/api/auth.js` | register/policy/changePassword/logoutAll/roleRequests/admin APIs；统一 401/403 处理 |
| `frontend/src/components/LoginView.vue` | 登录/注册双 Tab；注册字段校验与错误映射；注册成功自动登录 |
| `frontend/src/components/ChangePassword.vue` | 旧密码/新密码/确认；强制改密模式 |
| `frontend/src/components/AdminPanel.vue` | 用户列表/搜索/启停/角色/重置密码；角色申请审批 |
| `frontend/src/components/AppHeader.vue` | 用户菜单：修改密码、退出所有设备、用户管理（仅 admin）、退出登录 |
| `frontend/src/App.vue` | 强制改密门禁、管理员面板开关、注册/登录态恢复 |
| `frontend/src/api/procurement.js` | 删除硬编码身份头，改用 Cookie，401 通知 |
| `frontend/src/api/history.js` | 已完成 Cookie/401，补充权限错误提示 |

## 8. 测试策略

| 范围 | 测试 |
| --- | --- |
| 注册 | 成功自动登录、字段校验、重复用户名、弱密码、限流、原子性 |
| 登录/状态 | disabled、失败限流、must_change_password、最近登录时间 |
| 改密/重置 | 旧密码错误、策略、吊销其他会话、临时密码一次性、强制门禁 |
| 会话 | logout-all、禁用即时失效、角色变更即时生效 |
| 角色申请 | 创建、重复 pending、批准/拒绝、自己审批禁止、admin 不可申请 |
| 管理员 | 权限 403、列表/搜索、启停、改角色、最后 admin 保护、自我修改禁止 |
| 权限收口 | 采购 API 忽略 `X-User-Id`/`X-Roles`、历史隔离、admin 不可读消息正文 |
| 回归 | 001 全部测试 + 前端构建 |

测试替身：`tests/fakes.py` 增加 `sadd/srem/smembers`；用户仓储用 PostgreSQL 测试库；API 测试用依赖覆盖。

## 9. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 每次请求回查 DB 增加延迟 | 单条主键查询 + 连接池；后续可加短 TTL 缓存 |
| 管理员误操作锁死系统 | 禁止自我降级/禁用；最后 admin 保护；CLI 可恢复 |
| 临时密码泄露 | 仅返回一次、强制改密、吊销全部会话、不落日志 |
| 注册滥用 | IP 限流 + 用户名唯一 + 密码策略；后续可加 CAPTCHA |
| 权限改造遗漏 | 以路由清单逐项测试 403；前端不作为安全边界 |
| 旧会话/旧用户兼容 | 幂等加列；旧会话缺少新字段用默认值；禁用用户下次请求失效 |

回滚：代码回滚；新增列/表为 additive；不删除旧用户数据；临时密码与角色申请数据保留。

## 10. 实施顺序

1. 用户表扩展、密码/用户名校验、注册/登录/改密/会话吊销。
2. `get_current_user` DB 回查 + 权限依赖 + admin 用户管理/角色申请接口。
3. 采购/历史/聊天权限收口与前端身份头清理。
4. 前端注册、强制改密、用户菜单、管理员面板。
5. 测试补齐、全量回归、README/证据。


## 11. 实施结果（2026-10-29）

- 注册开放、立即生效、默认 requester，注册成功自动登录；字段/密码校验、IP 限流、用户名校验已落地。
- `app_user` 幂等扩展 `display_name/must_change_password/last_login_at/password_changed_at`；新增 `app_role_request`。
- `get_current_user` 每次请求回查数据库，禁用、角色变更、强制改密即时生效；会话索引支持退出所有设备。
- 角色申请（approver）与管理员审批、用户列表/搜索/启停/改角色/重置临时密码已完成。
- 采购 API 改用会话身份与数据库角色，删除 `X-User-Id`/`X-Roles`/`guoyaxing`；历史会话按归属隔离，管理员不可读他人消息正文；前端采购请求改用 Cookie。
- 前端：登录/注册双 Tab、强制改密、用户菜单（改密/退出所有设备/用户管理）、管理员面板。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → **196 passed**；`cd frontend && npm run build` → 成功。
- 与方案偏差：无范围性偏差；管理员最后一人保护与“不能修改自己”共同防止锁死，未额外引入角色审批表以外的权限点。
