# 计划：启动降级与健康检查（013）

- 规格：`specs/013-health-readiness/spec.md`（已确认 2026-11-05）
- 目标：先起服务、后台初始化、`/live` 可用、`/ready` 反映真实依赖。

## 1. 方案

### 1.1 去掉导入期 Redis 依赖

`src/agent/config.py`：

- 删除模块级 `STORE.setup()`；
- 新增 `async def initialize_redis_store()`，用 `asyncio.to_thread(STORE.setup)` 懒初始化；
- `AgentLoader.initialize()` 在 ping Redis 之后调用它。

这样 Redis 不可用时应用仍可导入/启动，错误由后台初始化捕获。

### 1.2 后台初始化

`src/api_view/web_main.py`：

- lifespan 立即创建 `app.state.init_task = asyncio.create_task(_initialize_runtime(app))` 并 `yield`；
- `_initialize_runtime` 依次初始化：
  1. 认证用户库（`get_user_repository`）
  2. Agent（`agent_loader.initialize`）
  3. 采购 task runner（`get_task_runner`）
  4. 对账 worker（`get_reconciliation_worker` + `start`）
- 每步 `try/except`，错误写入 `app.state.startup_errors`（脱敏、截断），不阻塞其他步骤；
- shutdown 时等待/取消 init task，再按既有顺序关闭 worker、runner、沙箱、agent。

### 1.3 健康检查

新增 `src/api_view/health.py`：

- `/live`：恒 200，`status=alive`；
- `/ready`：逐项检查，全部通过 200，否则 503；
- `/health`：与 ready 同源，健康 200 且含 `healthy`，不健康 503 且含 `degraded`。

检查项：

| 检查 | 方式 | 超时 |
| --- | --- | --- |
| redis | `REDIS_CLIENT.ping()`（线程池） | 2s |
| postgres | `Database.acquire()` + `SELECT 1` | 2s |
| erp_mcp | 解析 `MCP_SERVER_CONFIG["erp-api"]["url"]`，TCP 连接 | 2s |
| sandbox | 解析 `SANDBOX_CONFIG.domain`，TCP 连接 | 2s |
| agent | `agent_loader.is_ready` + `last_error` | 即时 |
| procurement | `task_runner_ready()` | 即时 |

- 结果缓存 `HEALTH_CACHE_TTL_SECONDS`（默认 5s），用 `asyncio.Lock` 防止并发重复检查；
- 错误只返回 `error_type` + 掩码截断文本，不返回 DSN/密码；
- 响应包含 `startup_errors` 的 key 与脱敏信息，便于定位初始化失败。

### 1.4 未就绪的聊天

`src/api_view/api/chat.py`：

- `/chat/stream`、`/chat/{thread_id}/resume` 入口检查 `agent_loader.is_ready`；
- 未就绪返回 503 `{code: "AGENT_NOT_READY", message, retryable: true}`；
- 不进入 SSE 生成器。

`src/api_view/agent_loader.py`：

- 增加 `is_ready`、`last_error`；
- initialize 失败时记录脱敏错误，`_agent` 保持 None；
- close 时清空 `_agent`，避免关闭后仍被使用。

### 1.5 启动脚本

`start_web.py` 改为轮询 `/ready`（而不是 `/health`），失败时打印 checks；超时仍按 180s。

## 2. 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/agent/config.py` | Redis Store 懒初始化 |
| `src/api_view/agent_loader.py` | 调用 lazy store；`is_ready`/`last_error`；close 清理 |
| `src/api_view/health.py`（新增） | 健康检查 router、缓存、脱敏、TCP 检查 |
| `src/api_view/web_main.py` | 后台初始化、lifespan 改造、注册 health router |
| `src/api_view/api/chat.py` | 未就绪 503 |
| `src/agent/procurement/dependencies.py` | `task_runner_ready()` / `worker_ready()` |
| `start_web.py` | 等待 `/ready` 并打印 checks |
| `tests/test_health.py`（新增） | live/ready/health、降级、缓存、脱敏、未就绪聊天 |
| `README.md` | 健康检查与启动行为说明 |

## 3. 风险

| 风险 | 处理 |
| --- | --- |
| 后台初始化期间请求打到未就绪接口 | chat 503；其他接口按现有错误返回；`/ready` 明确 initializing |
| 初始化任务异常未捕获 | `_initialize_runtime` 顶层 try/except，写 startup_errors |
| shutdown 时 init task 未完成 | `wait_for` + cancel；不阻塞退出 |
| 健康检查被频繁调用拖垮依赖 | 5s 缓存 + 2s 单项超时 + 并发锁 |
| TCP 检查误判（端口开但服务坏） | 已知限制；协议级检查后续 |
| 健康响应泄露 DSN | 只返回 error_type + 掩码文本；不返回配置值 |
| 移除 `STORE.setup()` 影响依赖它的代码 | AgentLoader 初始化时显式调用；启动前不读取 Store |

## 4. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1/AC-2/AC-3/AC-4/AC-5 | 构造最小 FastAPI app + 注入 fake checks；断言状态码/字段 |
| AC-6 | 静态断言 config 无模块级 `STORE.setup()`；存在 lazy 函数 |
| AC-7 | `agent_loader` 失败记录 `last_error`；health 读 agent check |
| AC-8 | chat 未就绪返回 503（monkeypatch `is_ready=False`） |
| AC-9 | fake check sleep 超过 timeout，断言失败且响应不挂起 |
| AC-10 | 连续两次请求只调用一次 check（缓存） |
| AC-11 | 含 `postgresql://user:pass@host` 的错误被掩码 |
| AC-12 | 静态断言 start_web 使用 `/ready` |

## 5. 实施顺序

1. config lazy store + agent_loader 状态
2. health router + checks
3. web_main 后台初始化 + chat 503
4. start_web
5. 测试与文档
