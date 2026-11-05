# 规格：启动降级与健康检查

- 规格编号：`013`
- 状态：已确认（2026-11-05，按推荐默认值实施）
- 提出人：采购 Agent 使用者
- 日期：2026-11-05
- 关联需求：失败降级专项 P0-9；`/health` 假健康检查

## 1. 背景与目标

当前 FastAPI 启动期把 Redis、PostgreSQL、ERP MCP、OpenSandbox、Agent 组装全部
当成硬依赖，任何一项不可用，应用直接起不来；同时 `/health` 永远返回
`{"status":"healthy"}`，不反映真实依赖状态。更底层的问题是 `agent.config`
在导入期执行 `STORE.setup()`，Redis 不可用时应用连导入/启动都失败，运维只能看到
进程退出，拿不到可诊断的状态。

本次目标：

- 进程先起来，`/live` 始终可答；初始化在后台执行；
- `/ready` 反映真实依赖（Redis/PostgreSQL/ERP MCP/沙箱/Agent/采购 runtime）；
- `/health` 保留兼容入口，但不再假健康；
- 依赖失败时应用可启动并返回明确 503，而不是导入即崩；
- 依赖检查有超时、缓存和脱敏，不泄露连接串/密钥。

## 2. 用户故事

- 作为运维，我希望 Redis 挂掉时进程仍能起来，`/ready` 告诉我哪一项不可用，
  而不是只看到 uvicorn 启动失败。
- 作为前端/启动脚本，我希望在 Agent 初始化 30–60 秒期间能区分“还在初始化”和
  “初始化失败”，而不是盲目等待或误判健康。
- 作为开发者，我希望聊天接口在 Agent 未就绪时返回 503 + 明确 code，
  而不是 500/空响应。

## 3. 验收标准

| 编号 | 场景 | Given | When | Then |
| --- | --- | --- | --- | --- |
| AC-1 | 进程存活 | 应用已启动 | `GET /live` | 恒 200，`status=alive`；不检查外部依赖 |
| AC-2 | 初始化中 | Agent 仍在后台初始化 | `GET /ready` | 503，`status=initializing`，`agent.ok=false`，不阻塞请求 |
| AC-3 | 全部就绪 | Redis/PG/ERP MCP/沙箱/Agent/runtime 均可用 | `GET /ready` | 200，`status=ready`，逐项 `ok=true` |
| AC-4 | 单项失败 | 任一关键依赖不可用 | `GET /ready` | 503，`status=degraded`，失败项 `ok=false` + 脱敏错误类型，其他项仍返回 |
| AC-5 | 兼容入口 | 任一状态 | `GET /health` | 健康时 200 且含 `healthy`；不健康时 503 且含 `degraded`；不返回假 healthy |
| AC-6 | Redis 导入期 | Redis 不可用 | 应用启动 | 不因 `STORE.setup()` 导入失败；后台初始化记录错误，`/live` 可用，`/ready` 503 |
| AC-7 | 初始化失败 | Agent/MCP/沙箱初始化抛异常 | 启动后 | 异常被捕获并记录；应用继续运行；`/ready` 返回失败项；不反复重建沙箱 |
| AC-8 | 聊天未就绪 | Agent 未就绪 | 调用 `/api/chat/stream` 或 `/resume` | 503，`code=AGENT_NOT_READY`，`retryable=true`，不进入流式生成 |
| AC-9 | 依赖检查超时 | 某依赖挂起 | `GET /ready` | 单项在配置超时内返回失败；整体响应不被无限阻塞 |
| AC-10 | 缓存 | 5s 内重复请求 | `GET /ready` | 复用缓存，避免健康检查风暴；超过 TTL 重新检查 |
| AC-11 | 脱敏 | 依赖错误包含连接串/密码 | 返回 health | 不出现密码/完整 DSN；错误文本截断并掩码 |
| AC-12 | 启动脚本 | 应用启动 | `start_web.py` | 等待 `/ready` 200 后再拉起前端；失败时输出 checks |

## 4. 范围

**包含：**

- `agent.config` 的 Redis Store 懒初始化。
- 后台 runtime 初始化与错误记录。
- `/live`、`/ready`、`/health`。
- Redis/PostgreSQL/ERP MCP/沙箱/Agent/采购 runtime 检查。
- 聊天接口未就绪 503。
- 检查超时、缓存、脱敏、启动脚本适配。
- 单测。

**不包含：**

- 初始化失败后的自动重试/自愈（本次只做可观测，重启或运维处理）。
- MCP 协议级握手/工具列表校验（只做 TCP 可达性）。
- 独立认证库与采购库不同 DSN 时的双库检查（默认同库；后续可扩展）。
- 前端健康页、Prometheus 指标导出。

## 5. 约束与依赖

- 身份/权限仍由服务端认证决定；健康接口不需要登录，且不返回业务数据。
- 健康检查只读，不创建沙箱、不触发 Agent 初始化、不建表。
- `/ready` 失败不能隐藏真实原因，但也不能泄露 DSN、密码、密钥或完整 prompt。
- 后台初始化必须可被 shutdown 取消/等待，避免进程退出时悬挂。
- 现有 `/health` 语义要兼容启动脚本的 `healthy` 判断。

## 6. 未决问题

| 编号 | 问题 | 影响 | 负责人 | 结论 |
| --- | --- | --- | --- | --- |
| Q-1 | 检查超时/缓存？ | 健康检查延迟与风暴 | 需求方 | 已确认：单项 2s，缓存 5s，可配置 |
| Q-2 | 关键依赖范围？ | 是否 503 | 需求方 | 已确认：Redis/PostgreSQL/ERP MCP/沙箱/Agent/采购 runtime 全部为关键项 |
| Q-3 | 初始化失败是否自动重试？ | 自愈能力 | 需求方 | 已确认：本次不自动重试，只报告；重启/运维处理 |
| Q-4 | MCP/沙箱检查深度？ | 误判风险 | 需求方 | 已确认：TCP 可达性；协议级检查后续 |
| Q-5 | `/health` 状态码？ | 兼容性 | 需求方 | 已确认：健康 200，不健康 503，body 同时含 `status` 与 `checks` |

## 7. 验收证据

2026-11-05 实施证据：

- 单测（11 passed）：`tests/test_health.py`
  - `/live` 不依赖外部检查，恒 200；
  - 全部检查通过：`/ready` 200 ready，`/health` 200 healthy；
  - 单项失败：`/ready` 503 degraded，失败项 `ok=false`，其他项仍返回；
  - 初始化中：`/ready` 503 initializing；
  - 5s 缓存生效（重复请求只执行一次 check）；
  - check 超时返回 `TimeoutError`，请求不挂起；
  - DSN 密码被掩码为 `***@`；
  - Agent 未就绪时聊天入口返回 503 `AGENT_NOT_READY`；
  - agent_loader 的 `is_ready`/`last_error` 与 startup_errors 暴露；
  - 静态断言 `config.py` 无模块级 `STORE.setup()`、存在 `initialize_redis_store`，
    `web_main.py` 使用后台初始化，`start_web.py` 等待 `/ready`。
- 真实启动验证（本机）：
  - 应用可在 Redis/Agent 初始化未完成时启动；
  - `GET /live` → 200 `{"status":"alive"}`；
  - 初始化期间 `GET /ready` → 503 `{"status":"initializing", ...}`，
    `checks` 中 `agent.ok=false`、`procurement.ok=false`，其余依赖项仍返回；
  - `GET /health` → 503 `{"status":"degraded","phase":"initializing", ...}`；
  - 验证后已停止测试进程。
- 目标回归：`tests/test_health.py` + 既有模型/工具/委派测试。

已知限制：

- ERP MCP/沙箱只做 TCP 可达性检查，不校验 MCP 协议握手或沙箱健康；
- 默认只检查采购库 DSN；`AUTH_DATABASE_URL` 与采购库不同时未单独探活；
- 初始化失败后不自动重试，需要重启或运维处理；
- 健康结果缓存 5s，依赖恢复最多滞后一个 TTL；
- 后台初始化期间，除聊天外的部分业务接口可能先于 runtime 就绪被调用，
  依赖既有错误路径返回。
