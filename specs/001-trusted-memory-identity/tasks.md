# 任务清单：聊天入口可信身份与用户记忆隔离

- 对应规格：`specs/001-trusted-memory-identity/spec.md`
- 对应方案：`specs/001-trusted-memory-identity/plan.md`
- 状态：已完成（2026-10-29）

> 规则：一次提交能完成一条；先测试后实现；发现规格问题先改 `spec.md`。

## 阶段一：认证核心

- [x] T1 认证配置、密码哈希、用户仓储与开通脚本
  - 文件：`src/api_view/auth/{__init__,config,passwords,users}.py`、`scripts/manage_auth_users.py`
  - 内容：scrypt 哈希/校验、`app_user` 建表、按用户名查询、创建/改密/列出、user_id 校验
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_auth_passwords.py tests/test_auth_users.py -q`

- [x] T2 Redis 会话、登录限流与 `get_current_user` 依赖
  - 文件：`src/api_view/auth/{sessions,dependencies}.py`
  - 内容：不透明 token、SHA-256 key、TTL、吊销、429 限流、401、Origin 白名单
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_auth_sessions.py -q`

- [x] T3 登录/登出/me/开发登录路由与应用装配
  - 文件：`src/api_view/auth/router.py`、`src/api_view/web_main.py`
  - 内容：Cookie 属性、统一错误、仅开发模式 dev-login、CORS 白名单、启动初始化用户表
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_auth_api.py -q`

- [x] T4 认证测试补齐
  - 文件：`tests/test_auth_passwords.py`、`tests/test_auth_users.py`、`tests/test_auth_sessions.py`、`tests/test_auth_api.py`、`tests/fakes.py`
  - 覆盖：正确/错误密码、过期/吊销会话、限流、生产禁用 dev-login、401/403/429
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_auth_*.py -q`

## 阶段二：聊天入口与会话归属

- [x] T5 会话归属绑定与校验
  - 文件：`src/api_view/thread_ownership.py`、`tests/test_thread_ownership.py`
  - 内容：`claim`（SET NX）、`verify`、未绑定/他人会话 403 错误；Redis 最小 fake
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_thread_ownership.py -q`

- [x] T6 聊天接口接入认证与可信上下文
  - 文件：`src/api_view/api/chat.py`、`src/api_view/agent_loader.py`、`src/agent/schema.py`
  - 内容：stream/resume/state/history 认证与归属校验；`ProcurementContext` 传 `request_id/thread_id`；移除固定 `guoyaxing`；`create_config` 删除 user_id 默认
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_chat_identity.py -q`

- [x] T7 前端登录页与认证状态
  - 文件：`frontend/src/api/auth.js`、`frontend/src/components/LoginView.vue`、`frontend/src/App.vue`、`frontend/src/components/AppHeader.vue`
  - 内容：启动 `/me`、未登录显示登录页、登录后加载会话、退出登录、开发登录按钮仅 DEV；`fetch` 带 same-origin 凭据
  - 验证：`cd frontend && npm run build`

## 阶段三：记忆隔离

- [x] T8 记忆身份与路径校验、审计日志
  - 文件：`src/agent/memory/{identity,audit}.py`、`tests/test_memory_identity.py`、`tests/test_memory_logging.py`
  - 内容：`require_user_id`、`(user_id,)`、偏好路径、路径穿越/他人路径拒绝、白名单日志字段
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_identity.py tests/test_memory_logging.py -q`

- [x] T9 `UserMemoryBackend` 包装 StoreBackend
  - 文件：`src/agent/memory/user_memory_backend.py`、`tests/test_memory_backend.py`
  - 内容：覆盖全部读写/检索/上传下载方法；denied 不触达底层 Store；审计
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_backend.py -q`

- [x] T10 主 Agent 与记忆中间件切换
  - 文件：`src/agent/main_agent.py`、`src/agent/middlewares/{memory_update,context_injection}.py`、`src/agent/tools/procurement_task_tools.py`、`src/agent/procurement/task_runner.py`
  - 内容：`/memories/` 路由改 `UserMemoryBackend`；中间件经 backend 读写；日志去正文；移除 `guoyaxing` 回退
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_*.py tests/test_procurement_task_tools.py -q`

- [x] T11 隔离与零读写验收测试
  - 文件：`tests/test_memory_isolation.py`、`tests/test_chat_identity.py`
  - 覆盖：A 写 B 读、缺身份 401 且 Store 零调用、伪造路径、消息内身份不生效
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest tests/test_memory_isolation.py tests/test_chat_identity.py -q`

## 阶段四：验收

- [x] T12 全量回归与文档
  - 文件：`README.md`、`AGENTS.md`、`specs/001-trusted-memory-identity/spec.md`
  - 内容：认证环境变量、开通用户、开发登录、401/403、旧会话策略、002 已知缺口；回填 AC-1~AC-9 证据
  - 验证：`PYTHONPATH=src .venv/bin/python -m pytest -q`；`cd frontend && npm run build`

## 完成定义（DoD）

- [x] AC-1~AC-9 均有对应测试或可复现证据
- [x] 全量 pytest 通过，前端构建通过
- [x] 生产路径无 `guoyaxing` 记忆回退；无身份时零 Store 读写
- [x] 日志含可信 user_id/request_id/thread_id，不含记忆正文
- [x] README 与 plan 的接口契约一致
- [x] 002 待办（history 隔离、采购入口）明确记录
