# ERP 采购智能助手

基于 [Deep Agents](https://github.com/langchain-ai/deepagents) 与 [LangGraph](https://github.com/langchain-ai/langgraph) 构建的 ERP 采购领域多 Agent 应用。主 Agent 负责理解需求、维护用户偏好并调度子 Agent；两类子 Agent 分别承担采购分析与订单操作；业务数据通过 MCP 工具访问 Java ERP 后端。

## 功能特性

- **多 Agent 协作**：主 Agent 通过 `task` 工具把任务委派给 `procurement-analyst`（采购分析）或 `procurement-order`（订单操作），同一条消息中可并行委派多个任务。
- **采购分析**：供应商比价、物料行情、库存预警、订单明细查询，支持 26 种图表与地图可视化，最终输出结构化报告。
- **订单操作**：采购订单创建、修改与查询；关键字段缺失时通过 HITL 中断向用户补充信息，绝不猜测业务数据。
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
src/api_view/               FastAPI 后端（流式对话、历史会话、HITL 恢复）
src/skills/                 内置技能（main / procurement / order）
frontend/                   Vue 3 + Vite 聊天前端
```

## 运行前置

| 依赖 | 默认地址 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 后端与 Agent 运行时 |
| Node.js | 18+ | 前端开发服务器 |
| PostgreSQL | `postgresql:///erp_openclaw` | 采购审批、任务、订单执行记录（本机默认 Unix socket） |
| Redis | `redis://127.0.0.1:6379/0` | Checkpointer 与长期记忆存储 |
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

在项目根目录创建 `.env`（`langgraph.json` 会自动加载）：

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
- `PROCUREMENT_TEST_DATABASE_URL`：测试库，默认 `postgresql:///erp_openclaw_test`。
- `APP_ENV=production`：日志写入 `erp_agent.log`，否则输出到控制台。

### 4. 启动服务

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

`CompositeBackend` 按路径分流：`/memories/` 下的读写被路由到 Redis `StoreBackend`，命名空间为 `(user_id,)`；其余路径（`/analysis`、`/data`、`/skills`）落在 OpenSandbox 沙箱。`MemoryUpdateMiddleware` 会在每轮对话后自动维护 `recent_suppliers` 与 `recent_queries`。

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

