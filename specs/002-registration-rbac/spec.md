# 规格：自助注册、账号校验与基于角色的权限控制

- 规格编号：`002`
- 状态：已实现（2026-10-29）
- 提出人：待填
- 日期：2026-10-29
- 前置规格：`specs/001-trusted-memory-identity/spec.md`

## 1. 背景与目标

001 已交付服务端登录、会话与用户记忆隔离，但仍是“管理员开通账号”的最小闭环：

- 没有注册入口，新用户必须由 CLI 创建；
- 账号只有用户名/密码与 `is_active`，没有密码策略、强制改密、会话吊销；
- `roles` 只存储在会话与 `app_user` 中，除聊天会话归属外没有真正的接口级权限校验；
- 采购 API 仍信任客户端 `X-User-Id`/`X-Roles`，历史会话列表/读取/删除没有按用户隔离。

本需求把认证补成可运营的完整闭环：自助注册、服务端校验、固定角色权限、角色升级审批、管理员用户管理，并收口 001 的遗留权限缺口。

## 2. 已确认的产品决策

| 编号 | 决策 | 结论 |
| --- | --- | --- |
| D-1 | 注册方式 | 开放自助注册，注册成功后立即生效，默认角色 `requester`，并自动登录 |
| D-2 | 身份校验 | 用户名 + 密码；不接邮件/短信；管理员审批只用于角色升级，不阻塞注册 |
| D-3 | 权限模型 | 固定角色 `requester` / `approver` / `admin`，权限矩阵在后端代码中维护，前端只做入口显隐 |
| D-4 | 管理员来源 | CLI 开通首个管理员；之后只能由现有管理员授予/撤销 `admin` |
| D-5 | 密码策略 | 8–128 字符，至少包含一个字母和一个数字，不得与用户名相同或包含用户名；注册需二次确认 |
| D-6 | 用户标识 | `user_id` 由服务端生成（`u_` + 12 位十六进制），登录名 `username` 由用户填写且大小写不敏感唯一 |
| D-7 | 角色升级 | 用户只能申请 `approver`；`admin` 只能由管理员直接授予；审批通过后下一次请求即生效 |
| D-8 | 密码重置 | 无邮件自助找回；用户可凭旧密码改密，管理员可重置为一次性临时密码并强制下次登录改密 |
| D-9 | 会话 | 默认 8 小时；支持退出当前会话、退出所有设备；禁用账号或重置密码立即吊销该用户全部会话 |

## 3. 用户故事

- 作为新员工，我希望自己注册账号后立即使用普通申请人功能，不必等待管理员开通。
- 作为申请人，我希望申请审批权限时由管理员审核，而不是自助提权。
- 作为管理员，我希望查看/搜索用户、启停账号、调整角色、重置密码、审批角色申请。
- 作为审批人，我希望只能看到权限允许的采购任务，并在审批状态下操作。
- 作为普通用户，我希望只能看到自己的会话与记忆，无法通过伪造请求头或路径越权。
- 作为安全/运维人员，我希望登录、注册、改密、角色变更都有不含敏感信息的审计日志。

## 4. 角色与权限矩阵

| 能力 | requester | approver | admin |
| --- | --- | --- | --- |
| 聊天、自己的记忆 | 是 | 是 | 是 |
| 查看/删除自己的会话 | 是 | 是 | 是 |
| 查看他人会话 | 否 | 否 | 是（仅排障） |
| 创建采购任务 | 是 | 是 | 是 |
| 查看采购任务 | 仅自己 | 全部 | 全部 |
| 恢复/审批采购任务 | 仅自己创建且非审批节点 | 审批相关状态 | 全部 |
| 申请 approver | 是 | 否（已是） | 否 |
| 管理用户/角色/重置密码 | 否 | 否 | 是 |
| 审批角色升级 | 否 | 否 | 是 |

权限判定必须在后端接口/服务层完成；前端隐藏入口只用于体验，不作为安全边界。

## 5. 验收标准

### AC-1 注册成功

- Given 未登录用户提交合法 `username/display_name/password/confirm_password`
- When 调用 `POST /api/auth/register`
- Then 创建状态为 active、角色为 `requester` 的用户；返回用户信息并下发会话 Cookie（自动登录）；响应中不出现密码哈希。

### AC-2 注册校验与防滥用

- Given 用户名重复（大小写不敏感）、格式非法、密码不符合策略、两次密码不一致、显示名为空
- When 注册
- Then 返回 422/409 与可定位到字段的错误，不创建半成品用户；同一 IP 超过注册频率限制返回 429。

### AC-3 密码与凭据安全

- Given 任意注册/登录/改密/重置密码请求
- When 检查数据库、日志、错误响应
- Then 只保存 scrypt 哈希；日志与错误中不出现明文密码、密码哈希、会话 token；登录失败信息不区分“用户不存在/密码错误”。

### AC-4 登录、禁用与锁定

- Given 账号 active / disabled / 连续失败
- When 登录
- Then active 可登录并更新最近登录时间；disabled 返回 401 通用错误且不创建会话；同一用户名+IP 连续失败达到阈值返回 429。

### AC-5 接口级角色权限

- Given 用户角色为 requester/approver/admin
- When 调用管理员接口、查看他人采购任务、查看他人会话
- Then 无权限一律 403；不产生数据修改；approver 只能访问任务与审批允许的状态；admin 可管理用户但不能读取他人聊天内容（聊天仍仅归属者）。

### AC-6 角色升级申请与审批

- Given requester 提交 `approver` 申请
- When 管理员批准
- Then 用户角色增加 `approver`，下一次请求立即生效（不依赖重新登录）；同一用户只能有一个待审批申请；拒绝后保留原角色并记录原因；用户不能申请 `admin`，不能审批自己的申请。

### AC-7 管理员用户管理

- Given 管理员
- When 搜索/分页查看用户、启用/禁用、调整角色、重置密码
- Then 操作成功并记录审计；最后一个 active admin 不能被禁用或降级；管理员不能修改自己的角色/状态；重置密码生成一次性临时密码，账号标记 `must_change_password` 且吊销全部会话。

### AC-8 修改密码与强制改密

- Given 用户已登录
- When 提交旧密码+新密码+确认
- Then 旧密码错误返回 400/401 且不改密；新密码符合策略后更新哈希、吊销其他会话并保持当前登录；管理员重置的用户在改密前只能访问 `/auth/me`、`/auth/change-password`、`/auth/logout`、`/auth/logout-all`，访问业务接口返回 403 `password_change_required`。

### AC-9 会话与账号状态

- Given 用户已登录
- When 退出当前会话、退出所有设备、账号被禁用或密码被重置
- Then 对应会话立即失效；旧 Cookie 再调用 `/auth/me` 返回 401；禁用账号的已有会话在下一次请求即被拒绝。

### AC-10 001 遗留缺口收口

- Given 已登录用户调用采购/历史接口，或前端发起采购请求
- When 请求携带伪造 `X-User-Id`/`X-Roles`
- Then 服务端只使用会话身份与数据库角色；`procurement.py` 不再有 `guoyaxing` 回退；前端不再硬编码身份头；历史会话列表/消息/删除仅返回或操作当前用户（admin 例外）的数据。

### AC-11 审计

- Given 注册、登录成功/失败、改密、角色申请/审批、管理员启停/改角色/重置密码
- When 检查日志
- Then 包含事件类型、actor、target、client_ip、outcome、时间；不含密码、哈希、token、Cookie 或完整请求体。

### AC-12 回归

- Given 002 实施完成
- When 执行全量测试与前端构建
- Then 001 的 AC-1~AC-9 仍通过；登录、注册、聊天、采购、历史主流程可用。

## 6. 范围

**包含：**

- 开放注册、字段/密码校验、注册限流、自动登录。
- 用户表扩展（display_name、must_change_password、last_login_at 等）与幂等迁移。
- 固定角色权限矩阵、`require_roles` 依赖、admin 接口。
- approver 角色申请与管理员审批。
- 管理员用户管理（列表/搜索/启停/角色/重置密码）。
- 修改密码、强制改密、退出所有设备。
- 采购 API、历史 API、前端采购调用的身份与权限收口。
- 审计日志与测试。

**不包含（Out of Scope）：**

- 邮箱/手机号验证、邮件自助找回密码、SMTP/短信接入。
- OAuth/SSO、LDAP、MFA、CAPTCHA、风控画像。
- 可配置 RBAC（角色/权限点动态入库）、组织架构、数据行级权限。
- 用户自助注销、头像/个人主页、消息通知。
- 审计日志查询界面（本期只写结构化日志）。

## 7. 约束与依赖

- 复用 001 的 `app_user`、Redis 会话、scrypt 哈希、HttpOnly Cookie、Origin 白名单。
- PostgreSQL 为唯一用户事实源；角色与状态变更必须即时影响权限。
- 无邮件/短信服务，因此不做邮箱/手机验证与自助找回。
- 注册与登录必须在无外部 IdP 的环境可测试；使用内存 Redis 与测试库。
- 现有 `requester/approver/admin` 角色与采购领域语义保持一致，不新增第四种角色。

## 8. 未决问题

| 编号 | 问题 | 结论 |
| --- | --- | --- |
| Q-1 | 注册是否需要邮箱/手机号？ | 不需要，按 D-2 |
| Q-2 | `user_id` 是否允许用户自定义工号？ | 不允许，服务端生成；管理员可通过 CLI 建特定 ID |
| Q-3 | 是否需要管理员审批注册？ | 不需要，注册即生效；审批只用于角色升级（D-1/D-7） |
| Q-4 | 临时密码如何交付？ | 接口仅返回一次，由管理员线下告知；不落库、不发邮件 |
| Q-5 | admin 能否读取他人聊天？ | 不能；admin 仅可读会话元数据/删除会话用于排障，不读消息正文 |

## 9. 验收证据（2026-10-29）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 | `tests/test_auth_registration.py::test_register_success_auto_login`：201、默认 requester、自动登录、Cookie |
| AC-2 | `test_register_validation_errors_are_field_scoped`、`test_register_duplicate_username_is_case_insensitive`、`test_register_password_confirmation`、`test_register_rate_limited` |
| AC-3 | `tests/test_auth_password_change.py`、`tests/test_auth_api.py`；日志中不含密码；数据库只存 scrypt 哈希 |
| AC-4 | `tests/test_auth_api.py::test_inactive_user_cannot_login`、`test_login_rate_limited`；`get_current_user` 每次回查 DB |
| AC-5 | `tests/test_auth_admin.py::test_requester_cannot_access_admin_endpoints`、`tests/test_history_isolation.py`、`tests/test_procurement_auth.py` |
| AC-6 | `tests/test_auth_admin.py::test_role_request_flow_and_immediate_effect`、`test_role_request_reject`；`test_auth_users.py::test_role_request_and_admin_count` |
| AC-7 | `tests/test_auth_admin.py::test_admin_lists_and_updates_user`、`test_admin_cannot_modify_self`、`test_admin_reset_password_forces_change` |
| AC-8 | `tests/test_auth_password_change.py::test_change_password_success`、`test_force_password_change_blocks_business_endpoints` |
| AC-9 | `test_logout_all_revokes_session`、`test_disabled_user_session_is_invalid_immediately`、`test_role_change_takes_effect_without_relogin` |
| AC-10 | `tests/test_procurement_auth.py`（忽略伪造头、源码无 `X-User-Id`/`X-Roles`/`guoyaxing`）、`tests/test_history_isolation.py` |
| AC-11 | `src/api_view/auth/audit.py` 白名单字段；测试覆盖登录/角色/重置事件 |
| AC-12 | 全量回归：`196 passed`；`cd frontend && npm run build` 成功 |

### 已知限制

- 无邮箱/手机验证与自助找回密码；忘记密码由管理员重置。
- 注册只做 IP 频率限制，未接入 CAPTCHA。
- 管理员可看他人会话元数据，但不能读取消息正文。

