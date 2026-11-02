# 规格：长期记忆的查看、遗忘、过期与审计

- 规格编号：`006`
- 状态：已实现（2026-11-02）
- 提出人：待填
- 日期：2026-11-02
- 前置规格：`specs/004-preference-concurrency/spec.md`、`specs/005-memory-candidates/spec.md`

## 1. 背景与问题

当前长期偏好只有自动写入路径，没有完整的管理闭环：

- 用户看不到系统实际保存了哪些偏好；
- 无法删除单个近期供应商/查询，只能整文件覆盖；
- 无法清空全部长期偏好；
- `recent_suppliers` / `recent_queries` 没有时间信息，无法按时间过期；
- “删除会话”和“删除长期记忆”在界面上容易被混为一谈；
- 删除操作没有可审计记录，也没有定义删除后自动提取是否会立即重新写入。

本需求补齐记忆生命周期：查看 → 单条遗忘 → 清空 → 过期淘汰 → 审计。

## 2. 三类操作必须区分

| 操作 | 删除范围 | 不删除 |
| --- | --- | --- |
| 删除会话 | Redis Checkpoint、会话展示消息、`thread_id` 归属记录 | 用户偏好、采购任务、审批、订单执行记录 |
| 删除单条记忆 | 指定的 `recent_suppliers` 或 `recent_queries` 一项 | 其他 recent 项、显式偏好、采购任务 |
| 清空用户记忆 | 全部跨会话偏好（显式字段 + recent 项） | 采购任务、审批、订单、权限与安全配置 |

- 三类操作使用不同接口、不同按钮、不同审计事件，不得复用“删除会话”实现记忆清理。
- 清空记忆只操作 `/memories/{user_id}/preferences.md` 对应的偏好命名空间。
- 删除会话后，用户偏好必须仍然存在。

## 3. 数据模型（schema v3）

为支持单条删除、过期和查看时间，`recent_suppliers` / `recent_queries` 从字符串列表升级为带元数据的条目：

```yaml
schema_version: 3
revision: 7
preferred_output: table
preferred_chart_type: bar
preferred_currency: CNY
preferred_language: zh
recent_suppliers:
  - value: 博世
    created_at: 2026-10-01T08:00:00+00:00
    last_seen_at: 2026-10-20T09:30:00+00:00
recent_queries:
  - value: 刹车片价格对比分析
    created_at: 2026-10-18T10:00:00+00:00
    last_seen_at: 2026-10-18T10:00:00+00:00
```

模型：

```python
class RecentMemoryItem(BaseModel):
    value: str
    created_at: datetime
    last_seen_at: datetime
```

- `value` 非空、最长 500，列表内按 value 去重。
- `last_seen_at` 在相同值再次成为合法候选时更新；`created_at` 保持不变。
- 数量上限沿用：suppliers 10、queries 5。
- `schema_version` 当前为 3：
  - v0/v1/v2 读取迁移为 v3；
  - 旧字符串条目迁移时补 `created_at=last_seen_at=迁移时间`；
  - 显式偏好原样保留，`revision` 保留（v0/v1 为 0）；
  - `schema_version > 3` 拒绝。
- 未知字段、非法时间、非法类型继续拒绝，不写回。

## 4. 过期与衰减

| 类型 | TTL | 数量上限 |
| --- | --- | --- |
| `recent_suppliers` | 90 天（按 `last_seen_at`） | 10 |
| `recent_queries` | 30 天（按 `last_seen_at`） | 5 |

- 过期判定：`now - last_seen_at > TTL`。
- 淘汰时机：
  - 偏好提交（自动候选/显式保存/删除/清空）时剪枝；
  - 查看接口只返回未过期条目，并给出 `expires_at`；
  - 读取不为了剪枝而单独写文件，避免“看一眼就改 revision”。
- 同值候选再次出现时更新 `last_seen_at`，不会产生重复项。
- TTL 与上限可通过环境变量配置（建议 `PREFERENCE_SUPPLIER_TTL_DAYS=90`、`PREFERENCE_QUERY_TTL_DAYS=30`）。

## 5. 接口

所有接口要求已登录用户本人，复用 `require_password_ready`；管理员不能借此读取他人偏好。

### 5.1 查看

```http
GET /api/memory/preferences
```

响应：

```json
{
  "schema_version": 3,
  "revision": 7,
  "preferred": {
    "preferred_output": "table",
    "preferred_chart_type": "bar",
    "preferred_currency": "CNY",
    "preferred_language": "zh"
  },
  "recent_suppliers": [
    {"value": "博世", "created_at": "...", "last_seen_at": "...", "expires_at": "..."}
  ],
  "recent_queries": [],
  "limits": {"supplier_ttl_days": 90, "query_ttl_days": 30, "max_suppliers": 10, "max_queries": 5}
}
```

### 5.2 删除单条

```http
DELETE /api/memory/preferences/items
Content-Type: application/json

{"memory_type": "recent_suppliers", "value": "博世"}
```

- 只允许 `recent_suppliers` / `recent_queries`；其他类型 422。
- 精确匹配 value；不存在返回 404，不写文件。
- 成功返回 `{"deleted": true, "revision": n}`；只删除该项，其他偏好不变。
- 复用 004 的锁 + revision + 冲突重试。

### 5.3 清空全部

```http
DELETE /api/memory/preferences
```

- 重置为默认偏好：显式字段为 null、recent 列表为空，`revision` 自增。
- 返回 `{"cleared": true, "revision": n}`。
- **不得**删除 procurement_task、approval、order_execution、app_user、角色或安全配置。

### 5.4 删除会话（已有接口保持不变）

```http
DELETE /api/history/{thread_id}
```

- 仅删除 Checkpoint、展示消息与 thread 归属；不得触碰偏好 Store。
- 前端文案明确写“删除会话”，与“删除记忆”区分。

## 6. 前端

新增“我的记忆”面板（用户菜单入口），包含：

- 显式偏好展示（输出格式、图表类型、货币、语言）；
- 近期供应商/查询列表，显示最后使用时间；
- 每项“忘记”按钮；
- “清空全部记忆”按钮，二次确认文案明确“会删除显式偏好与近期记录，但不会影响采购任务/审批”；
- 删除会话仍在会话侧栏，文案为“删除会话”；
- 记忆面板不提供“删除所有会话”。

## 7. 审计

新增事件：

```text
memory.delete_item  reason=recent_suppliers outcome=ok/not_found
memory.clear        outcome=ok
```

- 记录 `user_id/request_id/thread_id/op/reason/outcome/revision`；
- **不得**记录被删除的 value、偏好正文、供应商名或查询文本；
- 删除失败也记录结果，但不记录正文。

## 8. 删除后的自动提取

- 单条删除或清空后，偏好文件中不再有该值；下一轮自动提取只能根据**当前轮**的新消息/新的合法工具结果重新生成候选。
- 不设置“永久黑名单”，否则用户未来合法关注同一供应商时无法恢复。
- 但自动提取不得从历史 AI 摘要、历史消息或已删除文件内容回填；候选生成仍只读当前轮。
- 若本轮对话再次从真实工具结果形成同值候选，则允许重新写入（符合“再次形成合法记忆候选”）。

## 9. 验收标准

### AC-1 查看

- Given 用户已有显式偏好与近期记录
- When 查询自己的记忆
- Then 返回结构化列表、时间与过期时间；不返回其他用户或采购任务数据。

### AC-2 删除单条不影响其他

- Given `recent_suppliers` 含 A、B，显式偏好 `table`
- When 删除 A
- Then 只剩 B；`preferred_output` 仍为 table；其他 recent 查询不变。

### AC-3 清空不影响采购事实

- Given 用户有采购任务/审批记录与偏好
- When 清空记忆
- Then 偏好为空；任务、审批、订单执行记录、账号角色仍存在。

### AC-4 过期与数量淘汰

- Given 最近查询超过 30 天或列表超过 5 条
- When 提交或查看
- Then 过期项不展示/不再保留；同值再次出现时刷新 last_seen，不产生重复。
- 供应商按 90 天、10 条执行同样规则。

### AC-5 删除会话不影响偏好

- Given 用户有偏好与会话
- When 调用删除会话
- Then 会话/Checkpoint 删除，偏好文件内容与 revision 不变。

### AC-6 审计不保留正文

- Given 删除单条或清空
- When 检查审计日志
- Then 包含操作、用户、结果；不包含被删除的 value、供应商名或查询文本。

### AC-7 删除后不立即复活

- Given 删除供应商 A
- When 下一轮对话没有新的合法候选
- Then A 不会因旧消息/AI 摘要/自动提取重新写入。

### AC-8 非法操作

- Given 删除不存在的项、删除显式字段、清空时 Store 写失败
- Then 分别返回 404/422/明确失败；不产生部分写入。

### AC-9 回归

- Given 实施完成
- When 全量测试
- Then 003/004/005 的 schema、锁、候选、显式保存测试仍通过。

## 10. 范围

**包含：**

- schema v3 recent 条目与迁移。
- TTL/数量淘汰。
- 查看/单条删除/清空 API 与前端面板。
- 三类操作边界与审计。
- 删除后当前轮候选规则。
- 测试与文档。

**不包含：**

- 删除采购任务、审批、订单或账号数据；
- 管理员查看/删除他人记忆；
- 记忆回收站/恢复已删除项；
- 服务端定时后台清理任务（采用提交/查看时惰性淘汰）；
- 用户自定义 TTL 的 UI。

## 11. 待确认

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| Q-1 | recent 改为时间条目（schema v3）还是保留字符串+旁路元数据？ | **已确认**：改为时间条目 |
| Q-2 | TTL 默认值 | **已确认**：suppliers 90 天、queries 30 天；上限 10/5 |
| Q-3 | 清空是否包括显式偏好 | **已确认**：包括全部长期偏好 |
| Q-4 | 单条删除入口 | **已确认**：前端面板 + 可选聊天工具；清空前端二次确认 |
| Q-5 | 删除后是否加墓碑 | **已确认**：不加墓碑，当前轮合法候选可恢复 |

## 12. 验收证据（2026-11-02）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 查看 | `tests/test_memory_api.py::test_get_preferences_view` |
| AC-2 单条删除 | `test_delete_single_item_keeps_others`、`test_preference_service.py::test_delete_recent_item_keeps_other_preferences` |
| AC-3 清空不影响采购 | `test_clear_preferences`；清空只写偏好 Store，不触碰 task/approval/account |
| AC-4 过期/数量 | `test_preference_schema.py::test_prune_recent_by_ttl`、`test_preference_service.py::test_get_preferences_filters_expired_items`、`test_merge_respects_limits_and_keeps_newest` |
| AC-5 删除会话不影响偏好 | `history.delete_session` 只操作 Checkpoint/展示 key，不接入偏好服务；删除后偏好文件由 Store 独立保存 |
| AC-6 审计无正文 | `tests/test_memory_api.py::test_delete_audit_does_not_log_value` |
| AC-7 不复活 | 候选生成只读当前轮；删除后文件无该值，纯问候/无工具轮次无候选 |
| AC-8 非法 | `test_delete_missing_item_returns_404`、非法 memory_type 422、Store 失败不返回成功 |
| AC-9 回归 | 全量回归：`269 passed`；前端 `npm run build` 成功 |

### 实现摘要

- schema v3：`RecentMemoryItem(value/created_at/last_seen_at)`；v0/v1/v2 迁移。
- TTL：suppliers 90 天、queries 30 天；最多 10/5；查看过滤、提交剪枝。
- API：`GET /api/memory/preferences`、`DELETE /items`、`DELETE /preferences`。
- 前端「我的记忆」面板：查看、单条遗忘、清空二次确认；与删除会话文案分开。
- 审计：`memory.delete_item` / `memory.clear`，不记录被删除 value。
