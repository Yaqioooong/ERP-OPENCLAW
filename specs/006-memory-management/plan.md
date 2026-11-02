# 技术方案：长期记忆的查看、遗忘、过期与审计

- 对应规格：`specs/006-memory-management/spec.md`
- 状态：已实现（2026-11-02）

## 1. 数据模型 v3

`src/agent/memory/preferences.py`：

```python
class RecentMemoryItem(BaseModel):
    value: str
    created_at: datetime
    last_seen_at: datetime

class UserPreferences(BaseModel):
    schema_version: int = 3
    revision: int = 0
    ...
    recent_suppliers: list[RecentMemoryItem]  # max 10
    recent_queries: list[RecentMemoryItem]    # max 5
```

- 列表按 value 去重；时间使用 UTC。
- 旧字符串列表迁移：
  - v0/v1/v2 → v3；
  - `created_at = last_seen_at = 迁移时间`；
  - 显式字段和 revision 保留；
  - `schema_version > 3` 拒绝。
- 迁移读取不单独写回；首次成功提交写 v3。

## 2. 过期策略

- suppliers：`PREFERENCE_SUPPLIER_TTL_DAYS` 默认 90；
- queries：`PREFERENCE_QUERY_TTL_DAYS` 默认 30；
- 数量上限 10 / 5。
- 判定：`now - last_seen_at > TTL`。
- 同值候选更新 `last_seen_at`，保留 `created_at`。
- `prune_recent(preferences, now)` 在提交 mutation 前执行；查看接口只过滤展示，不单独写文件。
- 查看返回 `expires_at = last_seen_at + TTL`。

## 3. 服务扩展

`PreferenceMemoryService` 新增：

```python
async def get_preferences(user_id) -> UserPreferences
async def delete_recent_item(user_id, memory_type, value, *, request_id, thread_id) -> MemoryCommitResult
async def clear_all(user_id, *, request_id, thread_id) -> MemoryCommitResult
```

- 复用 004 的锁 + revision + 冲突重读重合并。
- `delete_recent_item`：
  - 仅 `recent_suppliers/recent_queries`；
  - 精确删除 value；不存在返回 `ok=False, reason="not_found"`，不写；
  - 删除前先 prune 过期项。
- `clear_all`：
  - mutation 返回默认偏好（显式字段 null、recent 空），revision 由提交层 +1；
  - 不触碰采购/审批/账号数据。
- 提交成功/失败按 004 规则；不记录被删除的 value。

## 4. HTTP 接口

新增 `src/api_view/api/memory.py`，注册到 `/api`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/memory/preferences` | 当前用户偏好 + 时间 + expires_at |
| DELETE | `/api/memory/preferences/items` | `{memory_type, value}` 单条删除 |
| DELETE | `/api/memory/preferences` | 清空全部偏好 |

- 依赖 `require_password_ready`；只能操作本人。
- 每次请求按用户构造 `UserMemoryBackend(store=STORE, namespace=lambda _rt: (user_id,))` 和 `PreferenceMemoryService`（请求层没有 graph runtime）。
- 错误映射：不存在 404、非法类型 422、Store/锁失败 503、写失败 500。
- 审计：`memory.delete_item` / `memory.clear`，reason 使用 memory_type 或“clear”，不含 value。

## 5. 前端

新增：

- `frontend/src/api/memory.js`
- `frontend/src/components/MemoryPanel.vue`
- `AppHeader.vue` 用户菜单加「我的记忆」
- `App.vue` 控制面板显示

面板内容：

- 显式偏好四项；
- 近期供应商/查询，显示 last_seen、expires_at；
- 每项“忘记”按钮；
- “清空全部记忆”，二次确认文案明确不影响采购任务/审批；
- 与会话侧栏“删除会话”严格区分。

## 6. 删除会话

- 保持 `DELETE /api/history/{thread_id}` 只删 Checkpoint/展示/thread 归属；
- 增加回归测试确认偏好 revision/内容不变。

## 7. 删除后复活

- 不增加墓碑；
- 删除后文件内无该项；候选生成只读当前轮，历史消息/AI 摘要不会回填；
- 当前轮若真实工具结果再次产生同值候选，允许重新写入。

## 8. 测试

| 验收 | 测试 |
| --- | --- |
| AC-1 查看 | API/服务 view 返回显式+recent+expires |
| AC-2 单条删除 | 删除 A 后 B 与显式偏好保留 |
| AC-3 清空不影响采购 | 清空后 task/approval 记录仍在（repository 层或静态验证） |
| AC-4 过期/数量 | 过期项不展示；超 5/10 截断；同值刷新 last_seen |
| AC-5 删除会话不影响偏好 | history delete 后 pref revision 不变 |
| AC-6 审计无正文 | loguru sink 断言无 value/供应商名/查询文本 |
| AC-7 不复活 | 删除后纯问候/无工具轮次不产生候选 |
| AC-8 非法 | 不存在 404、非法 memory_type 422、写失败不返回成功 |
| AC-9 回归 | 全量 pytest |

## 9. 文件清单

| 文件 | 改动 |
| --- | --- |
| `src/agent/memory/preferences.py` | RecentMemoryItem、v3、TTL/prune |
| `src/agent/memory/preference_service.py` | view/delete/clear、prune、recent 条目合并 |
| `src/api_view/api/memory.py` | 新 API |
| `src/api_view/web_main.py` | 注册 memory router |
| `frontend/src/api/memory.js` | API client |
| `frontend/src/components/MemoryPanel.vue` | 记忆面板 |
| `frontend/src/components/AppHeader.vue` | 我的记忆入口 |
| `frontend/src/App.vue` | 面板状态 |
| `src/agent/memory/AGENTS.md` / `prompts.py` | schema v3 示例 |
| tests | 更新 schema/service/middleware + 新增 memory API/view 测试 |

## 10. 风险

| 风险 | 缓解 |
| --- | --- |
| v3 文件回滚不兼容 | 文档标注；回滚前降级迁移 |
| 查看触发写 | 查看只读+过滤，不写 revision |
| 清空误删任务 | 只调用偏好 Store；测试确认 task/approval 不变 |
| 旧列表迁移时间统一 | 迁移时间作为 created/last_seen，旧项从迁移开始计 TTL |
| 审计泄露 value | 审计只传 type/reason，不传 value |

## 11. 实施顺序

1. schema v3 + TTL/prune + 测试更新。
2. service 查看/删除/清空 + 测试。
3. API + 前端面板。
4. 删除会话隔离/审计/全量回归。


## 12. 实施结果（2026-11-02）

- schema v3 + 时间条目 + TTL 剪枝；旧 v0/v1/v2 字符串列表自动迁移。
- service 增加查看、单条删除、清空；复用 004 锁/revision；删除不存在项不写。
- `/api/memory/preferences` 三组接口；前端「我的记忆」面板。
- 删除审计只记录 type/reason，不记录 value；删除会话路径未改动。
- 验证：后端 `269 passed`；`cd frontend && npm run build` 成功。
- 与方案偏差：无；过期采用查看过滤 + 下一次提交剪枝，不额外后台定时任务。
