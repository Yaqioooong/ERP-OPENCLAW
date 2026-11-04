# 技术方案：采购任务来源对话绑定

- 对应规格：`specs/011-task-conversation-origin/spec.md`
- 状态：已实现（2026-11-04，采用软绑定方案）
- 作者：实现方
- 日期：2026-11-04

## 1. 方案选型

| 方案 | 思路 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| A | 软绑定：任务记录可空的来源对话；创建时由服务端写入；只用于追溯/展示/查询，不参与权限和生命周期 | 改动小；任务可独立存活；兼容任务面板/API 创建；不破坏审批人跨对话查看 | 旧任务无来源；来源是逻辑引用，没有数据库外键 | 采用 |
| B | 硬绑定：来源对话必填；恢复必须同对话；删除会话级联删除任务 | 关系强 | 审批人、任务面板、独立任务无法工作；删除会话会破坏订单/审批事实 | 不采用 |

## 2. 总体设计

```text
聊天工具 / 任务面板 / API
  -> 服务端确定来源对话（聊天上下文或已校验的会话归属）
  -> ProcurementTaskRunner.start / start_background
  -> procurement_task.conversation_id
  -> TaskRunResult.conversation_id
  -> API 返回 conversation_id + conversation_available
  -> 任务面板显示来源，可返回来源对话
```

关键原则：

- `conversation_id` 是**来源**，不是任务所有者；任务授权仍按 `user_id` + 角色。
- 来源只由服务端确定：聊天入口取 `ProcurementContext.thread_id`；HTTP 入口的 `conversation_id` 必须先通过 `ThreadOwnershipStore` 校验。
- 来源写入后不再更新；恢复、审批、查询不改来源。
- 会话删除不级联删除任务；删除会话时释放归属 key，使任务详情能显示来源不可用。

## 3. 改动清单

| 层 | 文件/模块 | 改动类型 | 说明 |
| --- | --- | --- | --- |
| 领域模型 | `src/agent/procurement/models.py` | 修改 | `TaskRunResult` 增加 `conversation_id` |
| 任务服务 | `src/agent/procurement/task_runner.py` | 修改 | 所有结果带下来源；新增按对话查询方法 |
| 任务仓储 | `src/agent/procurement/task_repository.py` | 修改 | 新增 `list_by_conversation` 与 `(user_id, conversation_id, updated_at)` 索引 |
| Agent 工具 | `src/agent/tools/procurement_task_tools.py` | 修改 | 从 `runtime.context.thread_id` 取来源；缺失时拒绝创建；结果返回来源 |
| API | `src/api_view/api/procurement.py` | 修改 | 校验来源归属；返回 `conversation_available`；新增按对话查询接口 |
| 会话删除 | `src/api_view/thread_ownership.py`、`src/api_view/api/history.py` | 修改 | 删除会话时释放归属 key，避免已删除会话仍显示可用 |
| 前端 API | `frontend/src/api/procurement.js` | 修改 | 直接创建任务支持可选来源；新增按对话查询（供后续列表使用） |
| 前端面板 | `frontend/src/components/ProcurementTaskPanel.vue`、`frontend/src/App.vue` | 修改 | 展示来源对话；可访问时提供返回入口 |
| 测试 | `tests/` | 新增/修改 | 工具、Runner、仓储、API、会话删除、前端契约 |
| 文档 | `README.md`、`specs/011-task-conversation-origin/spec.md` | 修改 | 更新任务来源说明与验收证据 |

## 4. 接口与数据契约

### 4.1 Agent 工具

| 工具名 | 入参 | 出参 | 错误码/异常 |
| --- | --- | --- | --- |
| `start_procurement_task` | 不变（`request`） | 增加 `conversation_id` | 来源缺失返回 `CONVERSATION_CONTEXT_MISSING`，不创建任务 |
| `get_procurement_task` | 不变 | 增加 `conversation_id` | 不变 |
| `resume_procurement_task` | 不变 | 增加 `conversation_id` | 不变 |

### 4.2 HTTP 接口

| 方法 | 路径 | 请求体 | 响应体 | 权限 |
| --- | --- | --- | --- | --- |
| POST | `/api/procurement/tasks` | `message`、`request`、可选 `conversation_id` | `TaskRunResult` + `conversation_available` | `require_password_ready`；来源必须属于当前用户 |
| POST | `/api/procurement/tasks/{task_id}/resume` | `resume` | `TaskRunResult` + `conversation_available` | 任务 owner 或允许的 approver/admin |
| GET | `/api/procurement/tasks/{task_id}` | — | `TaskRunResult` + `conversation_available` | 任务 owner 或允许的 approver/admin |
| GET | `/api/procurement/tasks?conversation_id=&limit=&offset=` | — | `TaskListResponse` | 仅来源会话所有者 |

`conversation_available` 语义：

- 任务有来源，且当前用户是来源会话所有者：`true`。
- 任务无来源、来源已删除、来源属于其他用户：`false`。
- 审批人可查看任务详情中的来源标识，但通常为 `false`，不会获得会话正文。

`TaskListResponse`：

```json
{
  "conversation_id": "uuid",
  "tasks": [
    {
      "task_id": "TASK-...",
      "conversation_id": "uuid",
      "status": "awaiting_approval",
      "current_node": "request_approval",
      "created_at": "...",
      "updated_at": "...",
      "last_error_code": null,
      "last_error_message": null
    }
  ],
  "limit": 50,
  "offset": 0
}
```

### 4.3 数据模型

- 复用现有 `procurement_task.conversation_id`（可空），无需新增列。
- 新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_procurement_task_conversation
    ON procurement_task(user_id, conversation_id, updated_at DESC)
    WHERE conversation_id IS NOT NULL;
```

- 存量任务 `conversation_id` 保持 `NULL`，不回填。
- 来源字段只写一次，不提供更新路径。

## 5. 测试策略

| 验收标准 | 测试类型 | 用例/文件 | 说明 |
| --- | --- | --- | --- |
| AC-1、AC-2 | 单元/集成 | `tests/test_procurement_task_tools.py`、`tests/test_procurement_recovery.py` | 工具传来源；两个任务来源不同 |
| AC-3、AC-4、AC-5 | 单元/API | `tests/test_procurement_auth.py`、`tests/test_procurement_task_tools.py` | 无来源面板任务；非本人会话 403；缺失来源拒绝 |
| AC-6、AC-10 | Runner | `tests/test_procurement_recovery.py` | 跨对话恢复后来源不变 |
| AC-7、AC-8 | 仓储/API | `tests/test_task_repository.py`、`tests/test_procurement_auth.py` | 按对话过滤、分页、越权拒绝 |
| AC-9 | API | `tests/test_procurement_auth.py` | 审批人看到来源但 `conversation_available=false` |
| AC-11、AC-14 | 会话删除/API | `tests/test_thread_ownership.py`、`tests/test_history_isolation.py` | 删除释放归属；来源不可用 |
| AC-12 | 仓储/API | `tests/test_task_repository.py` | 旧任务 NULL 不影响查询 |
| AC-13 | 前端构建/手工 | `frontend/` | 任务面板展示来源和返回入口 |
| AC-15 | 单元/源码检查 | `tests/test_procurement_auth.py` | 不记录聊天正文；只传标识 |

## 6. 风险与回滚

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 旧任务无来源 | 历史任务显示“未知”，不能按对话查 | 明确不回填；UI 兼容 NULL |
| 来源是逻辑引用 | 会话删除后没有外键级联 | 保留标识；删除时释放归属 key；UI 显示不可用 |
| 客户端伪造来源 | 可能把任务挂到他人对话 | HTTP 入口必须校验 `ThreadOwnershipStore`；聊天入口只信服务端 context |
| 审批人看到来源标识 | 可能泄露会话存在性 | 只返回标识，不返回消息；按对话列表仅所有者 |
| 索引新增锁表 | 大表建索引可能阻塞 | 使用 `CREATE INDEX IF NOT EXISTS`；生产可改为并发建索引 |
| 前端旧响应无字段 | 旧任务面板不显示来源 | 字段可选；前端做空值兼容 |

回滚方式：

- 代码回滚后 `conversation_id` 列仍可保留，不影响旧逻辑。
- 新索引可保留；也可手工 `DROP INDEX`。
- `conversation_available` 是附加字段，旧前端忽略即可。

## 7. 实施顺序

1. 任务模型/仓储：结果带来源、按对话查询与索引。
2. Runner：来源透传、列表方法。
3. Agent 工具：聊天来源捕获与缺失拒绝。
4. API：来源校验、`conversation_available`、按对话查询。
5. 会话删除：释放归属 key。
6. 前端：面板来源展示与返回入口。
7. 测试、回归、README 与验收证据。
