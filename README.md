# ERP 采购智能助手

基于 [Deep Agents](https://github.com/langchain-ai/deepagents) 与 [LangGraph](https://github.com/langchain-ai/langgraph) 构建的 ERP 采购领域多 Agent 应用。主 Agent 负责理解需求、维护用户偏好并调度子 Agent；两类子 Agent 分别承担采购分析与订单操作；业务数据通过 MCP 工具访问 Java ERP 后端。

## 功能特性

- **多 Agent 协作**：主 Agent 通过 `task` 工具把任务委派给 `procurement-analyst`（采购分析）或 `procurement-order`（订单操作），同一条消息中可并行委派多个任务。
- **采购分析**：供应商比价、物料行情、库存预警、订单明细查询，支持 26 种图表与地图可视化，最终输出结构化报告。
- **订单操作**：采购订单创建、修改与查询；关键字段缺失时通过 HITL 中断向用户补充信息，绝不猜测业务数据。
- **完整采购任务**：前端「采购任务」面板和主 Agent 工具都可以启动“分析 → 审批 → 下单 → 核验”状态机。
- **注册、登录与权限**：支持自助注册（默认申请人）、登录会话、修改密码、退出所有设备；固定 `requester/approver/admin` 角色，管理员可审批角色升级、启停账号与重置密码。
- **可信身份与记忆隔离**：登录后由服务端会话确定 `user_id`，不接受客户端身份头；`/memories/` 强制按 `(user_id,)` 命名空间读写，路径越权与身份缺失都会被拒绝，并记录不含正文的审计日志。
- **长期记忆**：用户偏好按 `user_id` 隔离，持久化在 Redis Store 的 `/memories/{user_id}/preferences.md`，跨会话生效。
- **技能系统**：`/skills/` 下按主 Agent、采购、订单三个作用域组织技能，支持渐进式加载与用户自定义技能的持久化。
- **会话持久化**：Redis Checkpointer 保存对话状态，前端可浏览、恢复和删除历史会话。
- **可视化调试**：支持 LangGraph Studio 逐节点调试与 LangSmith 链路追踪。

## 目录结构

```text
langgraph.json              LangGraph 入口配置（graph 指向 src/agent/main_agent.py:agent）
start_web.py                一键启动前后端
src/agent/main_agent.py     主 Agent 组装：工具池、中间件栈、子 Agent、Sandbox
src/agent/config.py         模型、Redis、沙箱、路径与命名空间配置
src/agent/middlewares/      自定义中间件（上下文注入、记忆更新、技能同步与恢复）
src/agent/subagents/        子 Agent YAML 配置与加载器
src/agent/tools/            图表、技能分配、MCP 客户端、订单字段校验等工具
src/agent/backends/         OpenSandbox 沙箱后端封装
src/agent/memory/           主 Agent 系统提示词与 AGENTS.md
src/mcp_server/             FastMCP 工具服务，对接 Java ERP REST API
src/api_view/               FastAPI 后端（认证、流式对话、历史会话、HITL 恢复）
src/api_view/auth/          登录、会话、用户表与认证依赖
src/agent/memory/           记忆身份校验、审计与用户隔离 Backend
src/skills/                 内置技能（main / procurement / order）
frontend/                   Vue 3 + Vite 聊天前端
```

## 运行前置

| 依赖 | 默认地址 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 后端与 Agent 运行时 |
| Node.js | 18+ | 前端开发服务器 |
| PostgreSQL | `postgresql:///erp_openclaw` | 采购审批、任务、订单执行记录（本机默认 Unix socket） |
| Redis | `redis://127.0.0.1:6379/0` | Checkpointer、长期记忆与登录会话 |
| OpenSandbox | `http://127.0.0.1:8080` | 代码执行与文件操作沙箱 |
| Java ERP 后端 | `http://localhost:8081/api` | 业务数据源 |
| 本地 MCP 服务 | `http://127.0.0.1:8000/mcp` | ERP 工具暴露层 |

图表能力还会连接一个远程 MCP 服务（地址见 `src/agent/tools/mcp_client.py`），需要可访问外网。

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

前端依赖会在首次启动时自动安装，也可手动执行：

```bash
cd frontend
npm install
```

### 2. 初始化 PostgreSQL

采购业务表不再使用 SQLite。首次运行前创建数据库：

```bash
createdb erp_openclaw
createdb erp_openclaw_test
```

默认 DSN 为 `postgresql:///erp_openclaw`，使用当前系统用户和本地 Unix socket；
生产或远程数据库通过环境变量覆盖：

```text
PROCUREMENT_DATABASE_URL=postgresql://user:password@127.0.0.1:5432/erp_openclaw
PROCUREMENT_TEST_DATABASE_URL=postgresql://user:password@127.0.0.1:5432/erp_openclaw_test
```

表由应用启动时自动创建，不需要手工执行 DDL。

如果已有旧的 `data/procurement.db`，执行一次性迁移：

```bash
.venv/bin/python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path data/procurement.db \
  --database-url postgresql:///erp_openclaw
```

迁移完成后应用只读 PostgreSQL，SQLite 文件可以保留为备份。

### 3. 配置环境变量

在项目根目录创建 `.env`（`langgraph.json` 会自动加载）。认证相关变量见下方“登录与用户开通”：

```text
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=...
ALIBABA_API_KEY=...
ALIBABA_BASE_URL=...
ZHIPU_API_KEY=...
LANGSMITH_PROJECT=...
```

可选变量：

- `REDIS_URL`：默认 `redis://127.0.0.1:6379/0`。
- `PROCUREMENT_DATABASE_URL`：默认 `postgresql:///erp_openclaw`。
- `PROCUREMENT_REQUIRE_DISTINCT_APPROVER=true`：要求审批人与申请人不同（默认 `false`）。
- 采购 API 角色头：`X-User-Id`、`X-Username`、`X-Roles`（如 `requester` / `approver` / `admin`，逗号分隔）。
- `PROCUREMENT_TEST_DATABASE_URL`：测试库，默认 `postgresql:///erp_openclaw_test`。
- `APP_ENV=production`：日志写入 `erp_agent.log`，否则输出到控制台；生产环境强制安全 Cookie 并禁用开发登录。
- `AUTH_DATABASE_URL`：认证用户库，默认与采购库相同（`postgresql:///erp_openclaw`）。
- `AUTH_SESSION_TTL_SECONDS`：登录会话有效期，默认 `28800`（8 小时）。
- `AUTH_COOKIE_SECURE`：HTTPS 部署必须为 `true`；生产默认 `true`。
- `AUTH_ALLOWED_ORIGINS`：允许的前端来源，逗号分隔，默认 `http://localhost:3000,http://127.0.0.1:3000`。
- `AUTH_DEV_DEMO_USER_ID`：仅开发环境；配置后登录页出现“开发登录”入口，生产环境不会启用。
- `AUTH_REGISTER_MAX_ATTEMPTS` / `AUTH_REGISTER_WINDOW_SECONDS`：同一 IP 的注册频率限制，默认 5 次/小时。
- `AUTH_LOGIN_MAX_FAILURES` / `AUTH_LOGIN_WINDOW_SECONDS`：同一用户名+IP 的登录失败限制，默认 10 次/5 分钟。

### 4. 运行评测

30 个业务场景、故障注入和指标报告：

```bash
PYTHONPATH=src .venv/bin/python -m evals.cli \
  --output reports/eval_baseline.md
```

输出：

```text
reports/eval_baseline.md
reports/eval_baseline.json
```

### 5. 使用采购任务

前端页面右上角点击「采购任务」，填写物料、数量、仓库和预算即可启动完整流程；
面板会订阅任务 SSE，展示审批、分析报告、执行状态和订单结果。

也可以在聊天中直接说“走完整采购审批流程”，主 Agent 会调用：

```text
start_procurement_task
get_procurement_task
resume_procurement_task
```

主 Agent 创建任务后，前端会自动打开「采购任务」面板，后续审批通过按钮完成，不再依赖普通聊天输入。

### 6. 启动服务

一键启动前后端：

```bash
python start_web.py
```

- 前端：<http://localhost:3000>
- 后端：<http://localhost:8090>
- 接口文档：<http://localhost:8090/docs>

Agent 初始化（创建沙箱、加载 MCP 工具、组装子 Agent）通常需要 30–60 秒，启动脚本会等待 `/health` 就绪后再拉起前端。

分别启动各服务：

```bash
# MCP 工具服务
PYTHONPATH=src python -m mcp_server.server_main

# 后端
PYTHONPATH=src uvicorn api_view.web_main:app --reload --port 8090

# 前端
cd frontend && npm run dev
```

使用 LangGraph Studio 调试：

```bash
pip install "langgraph-cli[inmem]"
langgraph dev
```

### 7. 注册、登录与权限

认证用户表 `app_user` 与角色申请表由应用启动时自动创建。系统不提供默认账号，也不提供环境变量默认管理员。

**首次部署：用 CLI 开通第一个管理员**

```bash
PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py create \
  --user-id u_admin --username admin --display-name 管理员 --roles admin
```

之后的管理员可以在页面右上角「用户管理」中维护用户；也可以继续用 CLI：

```bash
PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py reset-password --user-id u_admin
PYTHONPATH=src .venv/bin/python scripts/manage_auth_users.py list
```

**普通用户**

- 登录页可直接注册；注册成功后立即生效并自动登录，默认角色 `requester`。
- 密码要求：8–128 位，至少包含字母和数字，不能包含用户名。
- 需要审批权限时，在用户菜单提交 `approver` 申请，由管理员审批；审批通过后下一次请求即生效，无需重新登录。
- 用户菜单支持修改密码、退出所有设备、退出登录。
- 管理员重置密码后会生成一次性临时密码，用户首次登录必须先改密，改密前业务接口返回 `403 password_change_required`。

**接口与错误**

- `POST /api/auth/register`、`GET /api/auth/policy`、`POST /api/auth/login`、`/logout`、`/me`、`/change-password`、`/logout-all`、`/role-requests`。
- `GET/PATCH /api/admin/users`、`POST /api/admin/users/{id}/reset-password`、`GET /api/admin/role-requests` 及审批接口，仅 `admin` 可访问。
- 未认证 `401`；无权限 `403`；重复用户名 `409`；字段错误 `422`；频率限制 `429`。
- 本地开发如需演示身份，可在 `.env` 设置 `APP_ENV=development` 和 `AUTH_DEV_DEMO_USER_ID=u_demo`，登录页会出现“开发登录”。生产环境该入口始终不存在。

**权限收口**

- 聊天与记忆：仅会话归属者可访问，管理员也不能读取他人聊天正文。
- 历史：普通用户只能看到自己的会话；管理员可看元数据但不能读他人消息正文，可删除会话用于排障。
- 采购：身份与角色来自数据库会话，客户端 `X-User-Id`/`X-Roles` 不再生效；approver/admin 可按权限查看任务，申请人只能看自己的。
- 旧会话没有归属信息，默认拒绝访问；请新建会话。

## 调用示例

```python
from langchain_core.messages import HumanMessage

from agent.main_agent import agent
from agent.schema import ProcurementContext

graph = await agent()
result = await graph.ainvoke(
    {"messages": [HumanMessage(content="对比 A、B 两家供应商的报价")]},
    config={"configurable": {"thread_id": "demo-thread"}},
    context=ProcurementContext(user_id="zhangsan", username="张三"),
)
```

`ProcurementContext` 通过 `context=` 传入，主 Agent 与子 Agent 都能读取；`thread_id` 用于关联 Redis 中的会话状态。

## 关键设计

### 主 Agent 与子 Agent 的通信

主 Agent 只能通过 `task` 工具与子 Agent 交互，两个方向各只有一个载体：

- **下发**：`task(description, subagent_type)`，子 Agent 以全新状态启动，只收到一条 `HumanMessage`；父 Agent 的对话历史不会传递。
- **回传**：子 Agent 最后一条有文本的 AI 消息被包装为 `ToolMessage` 返回，中间的工具调用、沙箱操作对父 Agent 不可见。

因此子 Agent 的 YAML 提示词中约定了固定的返回结构（报告路径、摘要、结论、建议），详细的 Markdown 报告落在沙箱 `/analysis/` 目录下。

### 上下文注入

| 通道 | 内容 | 作用范围 |
| --- | --- | --- |
| `context=` | `user_id`、`username` | 主 Agent 与子 Agent |
| `ContextInjectionMiddleware` | 当前用户身份与偏好文件路径 | 主 Agent |
| `memory=["/AGENTS.md"]` | 通用行为准则（随系统提示词注入） | 主 Agent |
| 技能同步 | 本地与持久化技能写入沙箱 `/skills/` | 沙箱文件层共享 |
| `task` 描述 | 【任务目标】【用户偏好】【需求正文】 | 主 Agent → 子 Agent |

### 用户偏好持久化

`CompositeBackend` 按路径分流：`/memories/` 下的读写被路由到 Redis `StoreBackend`，命名空间为 `(user_id,)`；其余路径（`/analysis`、`/data`、`/skills`）落在 OpenSandbox 沙箱。

偏好文件使用 Pydantic 模型 `UserPreferences` 严格校验（`schema_version: 1`）：

- `preferred_output` 只允许 `table` / `chart`；
- `preferred_chart_type` 只允许 `generate_visualization` 支持的图表类型；
- 未知字段、非法枚举、错误类型、缩进错误一律拒绝写入并告警，不覆盖原文件；
- 无版本或 `schema_version: 0` 的旧文件按 v0→v1 规则迁移，显式字段保留；
- `MemoryUpdateMiddleware` 每轮只自动维护 `recent_suppliers` / `recent_queries`，不修改显式偏好。

## 扩展开发

- **新增子 Agent**：在 `src/agent/subagents/configs/` 下新增 YAML，声明 `name`、`description`、`system_prompt` 与 `tools`，启动时自动加载。
- **新增技能**：在 `src/skills/{main,procurement,order}/` 下新建技能目录并编写 `SKILL.md`，运行时会同步到沙箱。
- **新增业务工具**：在 `src/mcp_server/tools/` 中注册 FastMCP 工具，并在 YAML 的 `tools` 列表里按工具名前缀引用。

## 常见问题

- **`pip install -r requirements.txt` 报编码错误**：该文件是 UTF-16 LE 编码（Windows 导出），可先转换后安装：

  ```bash
  iconv -f UTF-16 -t UTF-8 requirements.txt > requirements-utf8.txt
  pip install -r requirements-utf8.txt
  ```

- **首次请求很慢**：每个会话会新建沙箱并加载 MCP 工具，属正常初始化开销。
- **端口冲突**：后端 `8090`、前端 `3000`、MCP `8000`、沙箱 `8080`、Java 后端 `8081`，可在对应配置文件中调整。
- **历史会话为空**：确认 Redis 已启动且 `REDIS_URL` 指向正确的库。
- **登录后立即 401**：确认 `AUTH_ALLOWED_ORIGINS` 包含当前前端地址、浏览器允许 `erp_session` Cookie；生产必须使用 HTTPS 且 `AUTH_COOKIE_SECURE=true`。
- **无法继续旧对话**：001 起会话归属被强制校验，旧会话没有绑定用户会返回 403；请新建会话。

