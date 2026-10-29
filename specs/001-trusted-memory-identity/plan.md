# 技术方案：聊天入口可信身份与用户记忆隔离

- 对应规格：`specs/001-trusted-memory-identity/spec.md`
- 状态：已实现（2026-10-29）
- 作者：待填
- 日期：2026-10-29

## 1. 方案选型

### 1.1 认证方案

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A 自建服务端会话 | PostgreSQL 存用户与密码哈希，Redis 存不透明会话，浏览器持 HttpOnly Cookie | 可即时吊销；无 JWT 密钥轮换问题；复用现有 Redis/PostgreSQL；最贴合“可信会话” | 每次请求查一次 Redis；需补登录页与用户开通方式 | **采用** |
| B 自签 JWT | 本服务签发 HS256 JWT，前端放 Cookie 或内存 | 无状态、实现简单 | 吊销困难、需要密钥管理、过期/刷新策略更复杂 | 不采用 |
| C 信任网关注入头 | 由网关校验后注入 `X-User-Id` | 后端改动最小 | 当前没有网关；客户端可伪造头，等于没修 | 不采用 |

### 1.2 记忆隔离方案

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A 命名空间派生 + 专用 Backend 包装 | 命名空间强制 `(user_id,)`；在 `StoreBackend` 外包一层校验路径、审计日志 | 单一读写入口；可拒绝伪造路径；日志集中 | 需要包装全部 Backend 方法 | **采用** |
| B 仅改 `_user_namespace` | 只移除默认回退 | 改动最少 | 无法显式拒绝 `/memories/<他人>/...`，也缺审计 | 不够 |
| C 文件系统权限 | 依赖沙箱文件权限隔离 | 直观 | `/memories/` 实际走 Store，不落沙箱文件系统 | 不适用 |

### 1.3 密码哈希

不新增依赖，使用标准库 `hashlib.scrypt`（`n=2**14, r=8, p=1, dklen=32`），每个用户 16 字节随机盐，存储格式：

```text
scrypt$16384$8$1$<base64(salt)>$<base64(hash)>
```

校验使用 `hmac.compare_digest`；用户不存在时也执行一次 dummy 校验，降低用户名枚举的时序差异。

## 2. 总体设计

### 2.1 登录与会话

```text
浏览器
  │  POST /api/auth/login {username, password}
  ▼
FastAPI auth router
  │  1. Origin 白名单校验（有 Origin 时）
  │  2. 登录失败限流（Redis）
  │  3. 查 app_user，scrypt 校验密码
  │  4. 生成 32 字节随机 token，SHA-256 后作为 Redis key，TTL 8h
  │  5. Set-Cookie: erp_session=<token>; HttpOnly; SameSite=Lax; Path=/; [Secure]
  ▼
后续请求携带 Cookie（或 Authorization: Bearer <token>）
  ▼
get_current_user 依赖 → AuthenticatedUser(user_id, username, roles)
```

- 服务端只保存 token 的 SHA-256，不保存明文 token；Redis 丢失只会导致重新登录。
- 会话为绝对过期（默认 8 小时），不做滑动续期，避免会话无限延长。
- 登出删除 Redis key 并清除 Cookie。
- 登录失败限流：按 `username + client_ip` 计数，5 分钟内 10 次失败后返回 429；登录成功清零。
- CSRF：Cookie 使用 `SameSite=Lax`；登录/登出额外校验 `Origin`（若存在）必须在 `AUTH_ALLOWED_ORIGINS` 内。聊天接口为 POST + JSON，Lax 下不会随跨站表单提交。
- CORS：`allow_origins` 改为显式白名单并保留 `allow_credentials=True`，移除 `*`。

### 2.2 身份到记忆的链路

```text
Cookie/Bearer
  → get_current_user（401）
  → ProcurementContext(user_id, username, roles, request_id, thread_id)
  → LangGraph runtime.context
  → UserMemoryBackend._trusted_user_id()（缺失则抛 MemoryIdentityError）
  → namespace = (user_id,)
  → key 必须是 /{user_id}/...（否则 permission_denied）
  → StoreBackend（Redis Store）
```

- `ProcurementContext` 增加 `request_id`、`thread_id` 可选字段，并在构造时校验 `user_id` 非空且符合 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`；禁止 `.`、`..`。
- `/memories/` 路由从 `StoreBackend` 换为 `UserMemoryBackend`；它继承 `StoreBackend`，复用其存储逻辑，但所有读写先校验身份与路径，并写审计日志。
- `MemoryUpdateMiddleware` 不再直接操作 `runtime.store`，改为调用传入的 `backend`（即 `CompositeBackend`），走同一条校验与审计路径。
- `agent_loader.create_config` 移除 `user_id` 参数与默认值；身份只经 `context=` 传递，避免出现第二条身份通道。
- `procurement_task_tools` 中 `or "guoyaxing"` 的回退一并移除，缺失上下文时返回明确错误。

### 2.3 会话归属

```text
创建会话：thread_id 为空 → 服务端生成 UUID → Redis SET NX erp:chat:thread_owner:<id> = user_id
继续会话：thread_id 非空 → 读取 owner
  ├─ owner == 当前 user_id → 放行
  ├─ owner 缺失（旧会话） → 403（不自动认领，避免抢占）
  └─ owner != 当前 user_id → 403，不启动 Agent、不写展示消息/checkpoint
```

- 覆盖聊天路由下所有按 `thread_id` 访问的接口：`/chat/stream`、`/chat/{id}/resume`、`/chat/{id}`、`/chat/{id}/history`。
- 旧会话默认拒绝；如确需保留，提供一次性脚本按管理员指定绑定（不纳入 001 自动化）。
- 采购任务后台创建的 thread 不走聊天入口，其归属校验在 002 统一处理。

### 2.4 请求关联

- 聊天入口为每个请求生成 `request_id`（UUID），传入 `ProcurementContext`；`thread_id` 同时写入上下文。
- 记忆审计日志使用 `user_id + request_id + thread_id` 关联；不记录记忆正文、供应商名、查询文本。
- 日志字段示例（单行、键值或 JSON 均可，测试只断言字段存在与正文缺失）：

```text
memory_event op=read user_id=u_1001 namespace=u_1001 path=/u_1001/preferences.md
             thread_id=... request_id=... outcome=ok
```

## 3. 改动清单

| 层 | 文件/模块 | 改动类型 | 说明 |
| --- | --- | --- | --- |
| 认证配置 | `src/api_view/auth/config.py` | 新增 | 环境变量、Cookie/TTL/限流/开发登录开关 |
| 密码 | `src/api_view/auth/passwords.py` | 新增 | scrypt 哈希与校验 |
| 用户 | `src/api_view/auth/users.py` | 新增 | `app_user` 表、仓储、开通/改密 |
| 会话 | `src/api_view/auth/sessions.py` | 新增 | Redis 会话创建/解析/吊销、失败限流 |
| 依赖 | `src/api_view/auth/dependencies.py` | 新增 | `get_current_user`、401/403、Origin 校验 |
| 路由 | `src/api_view/auth/router.py` | 新增 | `login` / `logout` / `me` / `dev-login` |
| 用户脚本 | `scripts/manage_auth_users.py` | 新增 | 创建用户、重置密码、列出用户 |
| 应用装配 | `src/api_view/web_main.py` | 修改 | 注册 auth 路由；初始化用户表；CORS 白名单 |
| 会话归属 | `src/api_view/thread_ownership.py` | 新增 | Redis 绑定/校验，403 错误 |
| 聊天 API | `src/api_view/api/chat.py` | 修改 | 注入 `get_current_user`；传 `ProcurementContext`；校验 thread 归属；移除固定用户 |
| 会话配置 | `src/api_view/agent_loader.py` | 修改 | 删除 `user_id` 默认参数 |
| 结构 | `src/agent/schema.py` | 修改 | 新增 `request_id`、`thread_id`；`user_id` 校验 |
| 记忆身份 | `src/agent/memory/identity.py` | 新增 | `require_user_id`、命名空间/路径/偏好 key 校验 |
| 记忆审计 | `src/agent/memory/audit.py` | 新增 | 统一日志字段，禁止正文 |
| 记忆后端 | `src/agent/memory/user_memory_backend.py` | 新增 | 包装 StoreBackend，路径校验 + 审计 |
| 主 Agent | `src/agent/main_agent.py` | 修改 | 路由改 `UserMemoryBackend`；移除默认命名空间；中间件传 backend |
| 记忆中间件 | `src/agent/middlewares/memory_update.py` | 修改 | 经 backend 读写；缺失身份拒绝；日志去正文 |
| 上下文注入 | `src/agent/middlewares/context_injection.py` | 修改 | 使用统一身份 helper，缺失时明确报错 |
| 任务工具 | `src/agent/tools/procurement_task_tools.py` | 修改 | 移除 `guoyaxing` 回退 |
| 任务上下文 | `src/agent/procurement/task_runner.py` | 修改 | 补充 `thread_id`/`request_id`（可选） |
| 前端认证 | `frontend/src/api/auth.js` | 新增 | `login/logout/me`，`credentials: 'same-origin'` |
| 前端登录 | `frontend/src/components/LoginView.vue` | 新增 | 登录表单、开发登录按钮（仅 DEV 构建） |
| 前端装配 | `frontend/src/App.vue` | 修改 | 启动时 `/me`；未登录显示登录页；登录后加载会话 |
| 前端头部 | `frontend/src/components/AppHeader.vue` | 修改 | 显示当前用户、退出按钮 |
| 文档 | `README.md`、`.env` 说明 | 修改 | 认证环境变量、开通用户、开发登录、401/403 |
| 测试 | `tests/test_auth_*.py`、`tests/test_memory_*.py`、`tests/test_chat_identity.py` | 新增 | 覆盖 AC-1~AC-9 |

## 4. 接口与数据契约

### 4.1 认证接口

| 方法 | 路径 | 请求 | 成功响应 | 失败 |
| --- | --- | --- | --- | --- |
| POST | `/api/auth/login` | `{"username": "...", "password": "..."}` | `200 {"user": {"user_id","username","roles"}}` + Set-Cookie | 401 凭据错误；429 限流；403 Origin 不允许 |
| POST | `/api/auth/logout` | 无 | `204` + 清除 Cookie | 403 Origin 不允许 |
| GET | `/api/auth/me` | Cookie/Bearer | `200 {"user": {...}}` | 401 |
| POST | `/api/auth/dev-login` | 无 | `200` + Set-Cookie（仅开发模式） | 404（未启用） |

Cookie 属性：

```text
erp_session=<opaque-token>; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800
  生产：Secure；开发：由 AUTH_COOKIE_SECURE 控制（默认 false）
```

所有失败响应保持 FastAPI 默认 `{"detail": "..."}`；401 不得泄露用户是否存在。

### 4.2 用户与会话数据结构

```sql
CREATE TABLE IF NOT EXISTS app_user (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    roles         TEXT[] NOT NULL DEFAULT '{}',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_user_username_lower
    ON app_user (lower(username));
```

Redis：

| Key | Value | TTL |
| --- | --- | --- |
| `erp:auth:session:<sha256(token)>` | `{"user_id","username","roles"}` | 28800s |
| `erp:auth:fail:<lower(username)>:<ip>` | 计数器 | 300s |
| `erp:chat:thread_owner:<thread_id>` | `user_id` | 永久（随会话删除维护，002 处理） |

### 4.3 聊天接口变化

| 接口 | 变化 |
| --- | --- |
| `POST /api/chat/stream` | 增加认证依赖；`thread_id` 为空时服务端生成并绑定；非空时校验归属；`ProcurementContext` 由当前用户构造 |
| `POST /api/chat/{thread_id}/resume` | 增加认证依赖 + 归属校验；失败 403 且不进入流 |
| `GET /api/chat/{thread_id}` | 增加认证依赖 + 归属校验 |
| `GET /api/chat/{thread_id}/history` | 增加认证依赖 + 归属校验 |

401 在依赖阶段抛出，先于 `StreamingResponse` 建立，前端收到的是普通 HTTP 错误而非 SSE `error` 事件。

### 4.4 记忆路径规则

- 命名空间：固定 `(user_id,)`。
- 偏好文件 key：`/{user_id}/preferences.md`；对外路径：`/memories/{user_id}/preferences.md`。
- 拒绝：不以 `/{user_id}/` 开头的 key、包含 `..`、`//`、`\`、NUL、`%2e`/`%2f`（大小写不敏感）的路径、其他用户的路径段。
- `ls("/")`、`grep(path=None or "/")` 允许，范围仍由命名空间限制。
- 违规返回 `error="permission_denied"`，并记录 `reason=path_not_owned|path_traversal|invalid_identity`。

### 4.5 审计日志字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `event` | 是 | `memory.read` / `memory.write` / `memory.edit` / `memory.delete` |
| `user_id` | 是 | 与请求认证身份一致 |
| `namespace` | 是 | 实际 Store 命名空间 |
| `path` | 是 | 记忆路径（不含正文） |
| `request_id` / `thread_id` | 至少一个 | 请求关联 |
| `outcome` | 是 | `ok` / `denied` / `error` |
| `reason` | 否 | 拒绝或错误分类 |
| 正文类字段 | 禁止 | 文件内容、供应商名、查询文本、摘要原文 |

### 4.6 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `development` | `production` 时强制安全 Cookie、关闭开发登录 |
| `AUTH_DATABASE_URL` | `postgresql:///erp_openclaw` | 用户表所在库；可与采购库同库不同表 |
| `AUTH_SESSION_TTL_SECONDS` | `28800` | 会话绝对过期时间 |
| `AUTH_COOKIE_NAME` | `erp_session` | Cookie 名 |
| `AUTH_COOKIE_SECURE` | 生产 `true`、开发 `false` | HTTPS 下必须为 true |
| `AUTH_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS 与 Origin 校验白名单，逗号分隔 |
| `AUTH_LOGIN_MAX_FAILURES` | `10` | 窗口内最大失败次数 |
| `AUTH_LOGIN_WINDOW_SECONDS` | `300` | 限流窗口 |
| `AUTH_DEV_DEMO_USER_ID` | 空 | 仅开发模式；非空才启用 `/auth/dev-login` |
| `AUTH_DEV_DEMO_USERNAME` | 空 | 开发演示用户名，空则用 user_id |
| `AUTH_DEV_DEMO_ROLES` | `requester` | 开发演示角色 |

## 5. 测试策略

| 验收标准 | 测试类型 | 用例/文件 | 说明 |
| --- | --- | --- | --- |
| AC-1 | 集成（InMemoryStore） | `tests/test_memory_isolation.py` | A 写、B 读；断言 B 为空、A 命名空间存在 |
| AC-2 | API + 依赖覆盖 | `tests/test_chat_identity.py` | 无 Cookie、过期/伪造 token → 401；Store spy 零调用；Agent 未启动 |
| AC-3 | API + 工具调用 | `tests/test_chat_identity.py` | 消息/工具参数中的“我是 B”不改变身份；伪造 `X-User-Id` 无效 |
| AC-4 | 单元 | `tests/test_memory_identity.py` | 命名空间严格为 `(user_id,)`；空/非法 user_id 抛错 |
| AC-5 | 单元（Backend） | `tests/test_memory_backend.py` | `/B/...`、`..`、编码变体 → denied，底层 Store 未被调用，产生拒绝日志 |
| AC-6 | 单元 + 静态检查 | `tests/test_memory_identity.py` | 缺上下文时 namespace/中间件拒绝；指定模块不含 `guoyaxing` |
| AC-7 | 日志捕获 | `tests/test_memory_logging.py` | 捕获 loguru sink；同一 user_id/request_id；断言无正文/供应商名 |
| AC-8 | 回归 | 现有测试 + `tests/test_chat_flow.py` | 正常写入后新会话读回偏好；旧用例适配认证 |
| AC-9 | 单元 + API | `tests/test_thread_ownership.py` | A 绑定后 B 访问 stream/resume/state/history 均 403；B 不触发 Agent/Store 写 |
| 认证核心 | 单元 | `tests/test_auth_passwords.py`、`tests/test_auth_sessions.py`、`tests/test_auth_api.py` | scrypt 校验、会话过期/吊销、登录/登出/me、限流、开发登录仅开发可用 |
| 用户仓储 | 集成 | `tests/test_auth_users.py` | 依赖现有 PostgreSQL fixture，不可用时 skip |

测试替身：

- Store：`langgraph.store.memory.InMemoryStore`。
- Redis：`tests/fakes.py` 中实现 `get/set/del/incr/expire` 的最小内存版；认证单元测试不依赖真实 Redis。
- API：用 `httpx.AsyncClient(transport=ASGITransport(...))` 组装最小 FastAPI app，覆盖 `get_current_user` 与 `agent_loader`，避免启动沙箱/MCP。

## 6. 风险与回滚

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 自建认证引入新的攻击面 | 登录被暴力破解、会话被盗 | scrypt + 限流 + HttpOnly/SameSite + 绝对过期；不记录 token/密码；登录失败信息统一 |
| Cookie/CORS 配置错误 | 登录后请求 401/浏览器拒收 Cookie | 显式 Origin 白名单；Vite 代理同源；启动日志打印 Cookie 安全配置（不打印密钥） |
| 旧会话全部 403 | 演示数据不可继续 | 文档说明；提供绑定脚本；开发演示登录新建会话 |
| Backend 包装遗漏方法 | 某条路径绕过校验 | 覆盖 `BackendProtocol` 全部读写方法；参数化测试逐个方法 |
| 记忆中间件切换 backend 回归 | 偏好自动更新失效 | 用 InMemoryStore 端到端测试自动更新；保留现有偏好合并逻辑 |
| 采购/历史入口仍未认证 | 安全修复不完整 | 002 立即跟进；README 与验收报告中显式标注已知缺口 |
| 生产启动时无用户 | 无人能登录 | 部署前执行开通脚本；启动日志警告“用户表为空”；不自动创建默认账号 |
| 记忆正文进入日志 | 隐私泄露 | 审计函数只接受白名单字段；测试断言敏感文本不出现 |

回滚：

- 代码回滚即可恢复；`app_user` 为新增表，无破坏性迁移；会话 key 会自然过期。
- 旧 `guoyaxing` 记忆数据不修改、不删除（Q-4 暂定）。
- 生产不提供“关闭认证”的开关；故障时通过恢复上一版本处理，避免留下绕过路径。

## 7. 实施顺序

1. **认证核心**：配置、密码、用户仓储、会话、依赖、路由、CLI，配套单测。
2. **接入聊天**：`web_main` 装配、CORS、chat 依赖与上下文、thread 归属、前端登录页与 `/me`。
3. **记忆隔离**：identity/audit/backend、主 Agent 路由、中间件改造、移除默认用户，配套隔离与日志测试。
4. **验收**：A/B 隔离、401 零 Store 读写、路径伪造、跨用户 thread 403、全量回归、README 与验收证据。

## 8. 待确认（进入编码前）

- Q-4：旧 `guoyaxing` 记忆是否按“保留不迁移”执行？
- Q-5：日志中 `user_id` 明文是否可以？保留期是否沿用现状？
- 会话时长 8 小时、登录限流 10 次/5 分钟、Cookie 名 `erp_session`、`SameSite=Lax` 是否接受？
- 旧会话默认 403、需要新建会话，是否接受？

## 9. 实施结果（2026-10-29）

- 认证：`app_user` + Redis 服务端会话 + HttpOnly Cookie；登录/登出/me/开发登录、登录限流、Origin 白名单已落地。
- 聊天：`/chat/stream`、`/resume`、`/chat/{id}`、`/chat/{id}/history` 均接入认证；`thread_id` 首次创建时绑定用户，旧会话与他人会话返回 403。
- 记忆：`/memories/` 改由 `UserMemoryBackend` 路由；命名空间固定 `(user_id,)`，路径越权、穿越与编码变体被拒绝；`MemoryUpdateMiddleware` 经 backend 读写并去掉正文日志。
- 清理：`chat.py`、`_user_namespace`、`agent_loader.create_config`、`procurement_task_tools` 中的默认用户回退已移除。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → **196 passed（002 完成后）**；`cd frontend && npm run build` → 成功。
- 与方案的偏差：无范围性偏差；Backend 审计通过 `langgraph.runtime.get_runtime()` 读取 `request_id/thread_id`，读取失败时留空，不影响放行。
