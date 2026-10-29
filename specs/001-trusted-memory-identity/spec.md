# 规格：聊天入口可信身份与用户记忆隔离

- 规格编号：`001`
- 状态：已实现（2026-10-29；002 补齐注册、RBAC 与历史/采购权限）
- 提出人：guoyaxing
- 日期：2026-10-29
- 关联问题：聊天入口固定演示用户、记忆命名空间回退默认用户

## 1. 背景与目标

当前聊天链路没有可信的用户身份来源：

- `src/api_view/api/chat.py:245` 在每次请求中硬编码 `user_id=guoyaxing`，流式对话与中断恢复都使用该身份。
- `src/agent/main_agent.py:69-72` 的 `_user_namespace` 在运行时上下文缺失或 `user_id` 为空时回退到 `guoyaxing`，`/memories/` 的读写会落到默认用户命名空间。
- `src/api_view/agent_loader.py:79` 的配置构造同样保留 `guoyaxing` 默认值。
- 前端 `frontend/src/api/procurement.js` 直接硬编码身份头，`src/api_view/api/procurement.py:40` 在缺少头时回退 `guoyaxing`；`X-User-Id` 由客户端提供，本身不可信。

后果：身份链路一旦缺失或出错，请求仍会继续执行，并可能读取或写入错误用户的长期偏好，且现有日志无法证明记忆读写属于同一个可信用户。

本需求目标：建立“服务端可信身份 → 运行时上下文 → 记忆命名空间 → 审计日志”的唯一链路；身份不可用时拒绝记忆读写，而不是回退到默认用户。

## 2. 用户故事

- 作为已认证用户，我希望我的偏好只被我自己读写，以便我的供应商与输出习惯不会泄露给其他用户。
- 作为已认证用户，我希望身份异常时请求被明确拒绝，而不是在后台使用某个默认账号继续执行。
- 作为安全/运维人员，我希望每一条记忆读写日志都能关联到同一个可信用户和请求，且不包含偏好正文，以便审计而不扩大泄露面。
- 作为开发人员，我希望移除散落的默认用户常量，避免新增入口时再次引入同类问题。

## 3. 验收标准

### AC-1 用户间记忆隔离

- Given 用户 A 已认证并写入一条偏好（如“以后都用表格”）
- When 用户 B 以新会话读取偏好
- Then B 的上下文与记忆读取结果中不出现 A 的任何偏好内容；Store 中 A 的命名空间有该文件，B 的命名空间不存在该文件。

### AC-2 身份缺失时拒绝且无 Store 读写

- Given 请求未携带可验证的身份（缺失、过期、签名无效或不可解析）
- When 调用 `/chat/stream` 或 `/chat/{thread_id}/resume`
- Then 在建立 SSE 流之前返回明确的认证错误（HTTP 401，JSON 错误体）；Agent 不启动；不产生任何 `/memories/` 的 Store 读、写、删除操作。

### AC-3 身份不可由调用方或模型推断

- Given 请求已认证为用户 A
- When 请求体、消息正文、工具参数或模型输出中出现“我是用户 B”“读取 B 的偏好”等内容
- Then 实际身份与记忆命名空间仍为 A；调用方伪造 `X-User-Id` 等未受信头不能改变身份，也不能读取 B 的数据。

### AC-4 记忆命名空间唯一且精确

- Given 任意一次记忆读或写
- When 解析 Store 命名空间
- Then 命名空间严格等于 `(可信 user_id,)`；不包含默认用户、用户名、thread_id 或其他前缀/后缀；用户名为空不影响该结果。

### AC-5 伪造路径不能越权

- Given 用户 A 的 Agent 或调用方请求 `/memories/<B>/preferences.md`，或使用 `..`、重复斜杠、URL 编码等变体
- When 执行读或写
- Then 不返回 B 的任何数据，也不写入 B 的命名空间；结果为“不存在”或明确拒绝，并产生一条带可信身份的拒绝日志。

### AC-6 生产路径无默认用户回退

- Given 运行时上下文缺失、为空或 `user_id` 为空
- When 触发记忆读取或写入
- Then 操作被明确拒绝并记录错误；代码中不存在 `guoyaxing` 作为记忆链路的兜底身份；不出现“跳过校验后继续”的静默降级。

### AC-7 记忆读写日志可追溯且不含正文

- Given 任意一次记忆读或写（成功或失败）
- When 检查日志
- Then 同一请求内的日志都包含相同的可信 `user_id`，并可关联到 `thread_id` 或 `request_id`；日志包含操作类型、命名空间、结果；不包含偏好文件内容、供应商名称列表、查询正文或摘要原文。

### AC-8 正常使用不受影响

- Given 用户 A 已完成认证
- When A 写入偏好后开启新会话继续对话
- Then A 能读回并应用自己的偏好；上下文注入中的偏好路径与实际命名空间一致；原有聊天、恢复、任务入口的主流程不回归。

### AC-9 会话归属校验

- Given 用户 A 创建了会话 `thread_id`，该会话已绑定 A
- When 已认证用户 B 使用该 `thread_id` 发起流式对话、恢复中断或读取会话状态
- Then 返回 403，不启动 Agent、不读写该会话的展示消息/checkpoint，也不触发任何记忆写入；新建会话只能由服务端生成 `thread_id` 并在首次写入时绑定当前用户。

## 4. 非功能要求

- 认证失败必须在流开始前返回，不能先返回 200 再在 SSE 内报错。
- 拒绝日志需包含原因分类（缺失/无效/越权路径），但不得回显完整凭据或记忆内容。
- 记忆读写行为变化需有单元或集成测试覆盖，测试不得依赖真实生产 Store。

## 5. 范围

**包含：**

- 聊天入口（`/chat/stream`、`/chat/{thread_id}/resume`）的可信身份获取与传递。
- 自建最小认证：用户表、登录/登出/当前用户接口、服务端会话、HttpOnly Cookie；生产环境拒绝无凭据请求。
- 会话归属绑定与校验（聊天路由下所有按 `thread_id` 访问的接口），防止恢复他人会话。
- `ProcurementContext` 在记忆链路上的必填约束；`_user_namespace` 与配置构造中的默认用户移除。
- `/memories/` 路径与命名空间的一致性校验、越权路径拒绝。
- `MemoryUpdateMiddleware` 与相关记忆日志的字段规范。
- 覆盖 AC-1 至 AC-9 的自动化测试。

**不包含（Out of Scope）：**

- 自助注册、密码找回、多因素认证、SSO、角色管理后台；本次只提供最小登录/登出、当前用户查询和用户开通脚本。
- 会话列表、会话删除的按用户隔离（`history.py`），以及历史会话数据迁移；建议拆 002。
- `procurement.py` 采购任务 API 的 RBAC 改造与前端身份头清理（同源风险，建议拆 002；001 完成后采购入口仍是已知缺口）。
- 既有 `guoyaxing` 命名空间数据的迁移或清理策略。
- 记忆内容的加密存储与静态脱敏。

## 6. 约束与依赖

- 认证方式已确定（Q-1）：自建最小认证，不依赖外部网关。用户凭证存 PostgreSQL，服务端会话存 Redis，浏览器通过 HttpOnly Cookie 持有不透明会话令牌；不接受客户端 `X-User-Id` 作为生产身份。
- 开发/演示模式（Q-2）：仅当 `APP_ENV=development` 且显式配置演示用户时，提供开发登录入口；生产环境不存在该入口，缺少有效会话一律 401。
- 拆分策略（Q-3）：001 覆盖聊天入口、会话归属与记忆链路；`history.py` 会话列表/删除隔离、采购 API RBAC 与前端身份头清理拆到 002。
- 运行时上下文为 `agent.schema.ProcurementContext`，经 `context=` 传入 LangGraph；记忆路由为 `CompositeBackend` 的 `/memories/` → `StoreBackend`。
- Store 命名空间由 `_user_namespace(runtime)` 解析，路径前缀 `/memories` 会被路由层剥离后再作为 Store key。
- 依赖 PostgreSQL、Redis 与现有 pytest 设施；认证相关测试应可在无外部 IdP 的情况下运行。
- 兼容性：现有“未传身份直接可用”的本地调试方式将被破坏；旧会话没有归属信息，默认拒绝访问，需新建会话（迁移策略见 Q-4 与 002）。

## 7. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 生产环境可信身份从哪来：JWT 的签发方/算法/声明字段（如 `sub`）、网关注入的签名头，还是服务端会话？ | 决定认证适配层与测试方式；未定则 AC-2/AC-3 无法实现 | 待定 | **已确认**：无外部网关，自建最小认证；PostgreSQL 用户表 + Redis 服务端会话 + HttpOnly Cookie；不使用客户端身份头作为生产身份 |
| Q-2 | 开发/演示环境是否允许显式配置 demo 用户？若允许，是否限定 `APP_ENV=development` 且必须显式开启，生产一律 401？ | 影响本地体验与 AC-6 的判定 | 待定 | **已确认**：仅 `APP_ENV=development` 且显式配置 `AUTH_DEV_DEMO_*` 时提供开发登录；生产一律 401 |
| Q-3 | 本次是否一并处理 `history.py` 会话隔离与 `procurement.py` 默认身份？还是本规格只做聊天→记忆链路，其余拆 002？ | 决定范围与工作量，避免安全修复半途而废 | 待定 | **已确认**：001 只做聊天入口、会话归属与记忆链路；history/采购入口拆 002，002 紧随其后 |
| Q-4 | 既有 `guoyaxing` 命名空间下的记忆数据如何处理：保留只读、迁移给指定真实用户、还是清理？ | 影响灰度与回滚；不处理会造成新身份“记忆丢失”的体感 | 待定 | **已确认**：保留原数据、不自动迁移、不删除；新用户从空记忆开始，迁移脚本后续按需提供 |
| Q-5 | 日志中的 `user_id` 是否可明文记录（内部工号），还是需要哈希/掩码？日志保留多久？ | 影响日志字段与合规 | 待定 | **已确认**：记录明文内部 `user_id` 与 `request_id`，不记录偏好正文；保留期沿用现有日志策略 |
| Q-6 | 认证失败统一用 401，已认证但无权访问用 403，是否与现有前端错误处理兼容？ | 影响前端提示与 SSE 行为 | 待定 | **已确认**：401 表示未认证/会话失效，403 表示会话归属或路径越权；前端统一提示并跳转登录 |

## 8. 验收证据（2026-10-29）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 | `tests/test_memory_backend.py::test_user_a_write_is_invisible_to_user_b`、`test_composite_backend_routes_to_user_namespace`：A 写入后 B 读取不到 |
| AC-2 | `tests/test_chat_identity.py::test_missing_identity_returns_401_before_stream`、`tests/test_auth_api.py::test_me_without_cookie_is_401`：401 在建流前返回，流函数未被调用 |
| AC-3 | `tests/test_chat_identity.py::test_forged_user_header_cannot_change_identity`：伪造 `X-User-Id` 后上下文仍为认证用户；`tests/test_auth_api.py` 登录校验 |
| AC-4 | `tests/test_memory_identity.py::test_valid_user_id_and_namespace`、`test_memory_backend.py`：命名空间严格为 `(user_id,)` |
| AC-5 | `tests/test_memory_backend.py::test_forged_path_is_denied_without_touching_store` 及路径变体参数化；拒绝日志含 `path_not_owned` |
| AC-6 | `tests/test_memory_identity.py::test_memory_chain_modules_have_no_demo_identity_fallback`、`tests/test_memory_middleware.py::test_middleware_missing_identity_refuses_all_store_ops` |
| AC-7 | `tests/test_memory_backend.py::test_denied_operation_is_logged_without_content`、`test_audit_drops_unknown_and_content_fields`、`tests/test_memory_middleware.py::test_middleware_does_not_log_content` |
| AC-8 | `tests/test_memory_middleware.py::test_middleware_writes_only_own_preferences_path`；全量 pytest 回归通过 |
| AC-9 | `tests/test_chat_identity.py::test_user_b_cannot_resume_user_a_thread`、`test_user_b_cannot_read_user_a_thread_state`、`test_legacy_unowned_thread_is_rejected`、`tests/test_thread_ownership.py` |
| 认证核心 | `tests/test_auth_passwords.py`、`test_auth_sessions.py`、`test_auth_api.py`、`test_auth_users.py` |
| 回归 | `PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q`：002 完成后 196 passed；`cd frontend && npm run build`：构建成功 |
| 用户开通 | `PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py list`：用户表可正常初始化与读取 |

### 已知缺口（002 已收口）

- `history.py` 的会话列表、消息读取与删除已按用户过滤（管理员仅可看元数据/删除，不可读正文）。
- `procurement.py` 已改用会话身份与数据库角色；前端不再硬编码身份头。
- 旧 `guoyaxing` 记忆数据未迁移，旧会话默认 403；迁移策略另行处理。
