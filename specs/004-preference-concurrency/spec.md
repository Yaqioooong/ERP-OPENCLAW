# 规格：偏好记忆并发安全与可判定写入结果

- 规格编号：`004`
- 状态：已实现（2026-10-29）
- 提出人：待填
- 日期：2026-10-29
- 相关代码：[memory_update.py:229](src/agent/middlewares/memory_update.py:229)
- 前置规格：`specs/003-preference-schema-versioning/spec.md`

## 1. 背景与问题

当前自动记忆在 `MemoryUpdateMiddleware.aafter_agent` 中执行：

```text
backend.aread(preferences.md)
  → 解析 / 合并
  → backend.awrite(preferences.md, 整个文件)
```

这是非原子的“读取整文件 → 修改 → 写回”：

- 两个请求同时读取同一版本，各自合并后写回，后写者会覆盖前者的有效变更；
- `ReadResult.error` 只有“not found”会被当作空文件，但读取异常/错误与其他失败路径缺少统一契约，存在误把“读取失败”解释成“没有旧内容”的风险；
- 写回只通过日志观察，调用方拿不到明确成功/失败；
- 用户明确要求保存偏好时，Agent 仍可能用通用 `write_file`，无法向用户提供可靠的“已保存/未保存”结果；
- 没有内容版本号，无法判断读到的内容是否已被其他写入修改。

## 2. 目标

- 所有偏好提交都具备版本检查与互斥/乐观并发控制。
- 版本冲突时重新读取、重新合并，而不是覆盖。
- 读取失败必须失败关闭，不得降级为空文件。
- 写入返回结构化结果，调用方可判断成功/失败。
- 自动近期记忆可尽力而为；显式偏好保存必须向用户给出明确结果。
- 并发更新同一用户的不同有效变更不得丢失。

## 3. 术语

| 术语 | 含义 |
| --- | --- |
| `revision` | 偏好文件内容版本号，非负整数，每次成功提交 +1；与 `schema_version`（格式版本）不同 |
| 提交（commit） | 在锁/版本保护下完成“读取→合并→校验→写回”的一次完整操作 |
| 自动记忆 | `MemoryUpdateMiddleware` 维护的 `recent_suppliers` / `recent_queries`，尽力而为 |
| 显式偏好 | 用户明确要求保存的 `preferred_output` 等字段，必须返回保存结果 |
| 读取失败 | `aread` 抛异常，或返回非“文件不存在”的错误 |

## 4. 并发与版本策略（已确认）

LangGraph `BaseStore`/`RedisStore` 当前只提供 `aget`/`aput`，没有 compare-and-swap 或版本参数。因此采用**Redis 分布式互斥 + 文件内 revision 版本号**作为 CAS 的等价机制：

```text
1. 获取 per-user preferences 锁（Redis SET NX PX + token）
2. 锁内读取文件与 revision
3. 合并变更，生成 revision + 1 的新文件
4. 写回前再次读取 revision；不一致 → 释放锁并重新走 1~3
5. 写回（错误/异常 → 失败结果，不发布成功日志）
6. 释放锁（Lua 校验 token，避免误解锁）
```

- 锁保证同一用户偏好的提交串行化，避免“读旧写新”覆盖。
- revision 提供内容版本，用于冲突检测、审计和结果返回；不是 `schema_version`。
- 若评审要求严格 CAS 而非互斥锁，需要修改 Store 层或直接操作 Redis key；当前方案在共享 Redis 的多实例部署下等价成立。
- 所有应用实例必须共享同一 Redis；锁 TTL 用于进程崩溃自动释放。
- 超时/重试上限、锁 TTL 应可配置，默认建议：锁 TTL 10s、等待 2s、冲突重试 3 次。

### 4.1 读取失败规则

| 情况 | 处理 |
| --- | --- |
| `ReadResult.error` 表示文件不存在 | 允许使用默认偏好（只有这一种） |
| `ReadResult.error` 为其他错误 | `MemoryCommitResult(ok=False, reason="read_failed")`，不写 |
| `aread` 抛异常 | 同上，不写 |
| 文件存在但 YAML/模型非法 | `invalid_schema`，不写（沿用 003） |

禁止把“读取异常/超时/权限错误”当成空文件，从而覆盖已有显式偏好。

### 4.2 版本冲突

- 提交前读取 `revision`；写回前再次读取并比较。
- 若 revision 已变化：丢弃本次计算结果，重新读取最新文件并重新合并，最多重试 N 次。
- 若重试耗尽：返回 `conflict`，不写入。
- 自动更新失败可静默降级（记录结果）；显式保存失败必须告知用户。

## 5. 写入结果契约

所有偏好提交返回统一结构：

```text
MemoryCommitResult:
  ok: bool
  revision: int | None
  reason: "ok" | "read_failed" | "write_failed" | "invalid_schema"
          | "lock_timeout" | "conflict" | "permission_denied" | "not_found"
  retryable: bool
  message: str          # 可展示给用户的简短说明，不含偏好正文
```

- 只有 Store 写入成功且 revision 已更新后，才允许 `ok=True`、`revision=n`。
- 写异常、写返回错误、锁超时、冲突重试耗尽 → `ok=False`。
- 日志中的 `memory.update_preferences` / “已更新”只能在 `ok=True` 后出现。
- 失败日志只记录 `user_id/request_id/thread_id/reason/retryable`，不记录正文。

## 6. 自动记忆与显式偏好

### 6.1 自动近期记忆（尽力而为）

- `MemoryUpdateMiddleware` 调用统一的偏好提交服务，只更新 `recent_suppliers` / `recent_queries`。
- 失败不会阻断本轮对话；记录 `ok=False` 与原因。
- 不得在失败时向用户声称“已记住/已更新”。
- 并发自动更新不同供应商时，锁+revision 保证两次变更都保留。

### 6.2 显式偏好（必须可判定）

- 新增专用工具 `save_user_preferences`（名称待评审），接收显式字段：
  - `preferred_output` / `preferred_chart_type` / `preferred_currency` / `preferred_language`；
  - 不允许写 `recent_suppliers` / `recent_queries`（自动维护）；
  - 未提供的字段保持不变，显式 `null` 表示清除（具体语义待评审）。
- 工具返回：

```json
{"saved": true, "revision": 3}
```

或

```json
{"saved": false, "reason": "write_failed", "retryable": true,
 "message": "偏好未保存，请稍后重试"}
```

- Agent 必须根据 `saved` 向用户明确说明“已保存”或“未保存”；不得只凭日志或工具调用成功假定保存成功。
- 直接通过通用 `write_file` / `edit_file` 修改 `preferences.md` 必须被阻止（返回 `use_save_preferences_tool`），否则并发保护可被绕过。初始文件创建也走专用工具或受控默认创建。
- 若评审认为不应阻止 `write_file`，则需要在 Backend 层实现同样的锁+revision 合并，不能用非原子写入。

## 7. schema_version 与 revision

建议将 schema 版本从 `1` 提升到 `2`：

```yaml
schema_version: 2
revision: 0
preferred_output: table
preferred_chart_type: bar
preferred_currency: CNY
preferred_language: zh
recent_suppliers: []
recent_queries: []
```

迁移规则：

| 输入 | 处理 |
| --- | --- |
| 无 `schema_version` 或 `0` | v0 → v2；补默认值，`revision: 0`，保留显式字段 |
| `schema_version: 1` | v1 → v2；补 `revision: 0`，保留显式字段 |
| `schema_version: 2` | 当前版本 |
| `> 2` | `unsupported_version`，拒绝 |
| 未知字段/非法枚举/类型错误 | 拒绝，不写（沿用 003） |

- `revision` 必须是非负整数；成功提交后 +1。
- 迁移读取本身不写回；首次成功提交时写为 v2。
- 旧文件缺 `revision` 视为 0。

## 8. 验收标准

### AC-1 并发不同变更不丢失

- Given 两个并发自动更新，分别为同一用户加入供应商 A、B
- When 两次提交都成功
- Then 最终 `recent_suppliers` 同时包含 A、B；`revision` 递增 2 次；无一方的变更被覆盖。

### AC-2 读取异常不写

- Given `backend.aread` 抛出异常，或返回非 not-found 错误
- When 自动或显式提交
- Then 返回 `ok=False, reason=read_failed`；不调用任何写操作；原文件不变。

### AC-3 写入异常不误报成功

- Given Store 写入抛异常或返回错误
- When 提交
- Then 返回 `ok=False, reason=write_failed`；不产生“已更新/已保存”成功日志或成功工具结果；显式保存向用户返回“未保存”。

### AC-4 显式保存结果可达

- Given 用户明确说“以后都用表格”
- When Agent 调用 `save_user_preferences`
- Then 成功时用户收到“已保存”；失败时收到“未保存 + 原因”，不得假装成功。

### AC-5 冲突重读重合并

- Given 提交读取 revision 后，另一个合法提交先完成
- When 当前提交写回前检测到 revision 变化
- Then 重新读取最新内容并合并，重试成功则保留双方变更；重试耗尽返回 `conflict` 且不覆盖。

### AC-6 绕过路径被阻断

- Given Agent 对 `preferences.md` 调用通用 `write_file` / `edit_file`
- When 写入
- Then 返回 `use_save_preferences_tool`，不直接覆盖；显式偏好只能经专用工具保存。

### AC-7 版本迁移

- Given 无版本、v0 或 v1 文件
- When 读取
- Then 按规则迁移为 v2，`revision: 0`，显式字段保持不变；首次成功提交写回 v2。
- Given `schema_version: 3`
- Then `unsupported_version`，不写。

### AC-8 自动记忆失败不阻断对话

- Given 自动提交 `read_failed` / `write_failed` / `lock_timeout`
- When 对话继续
- Then 不抛到用户请求主流程，不声称已更新；记录结构化失败结果。

### AC-9 回归

- Given 实施完成
- When 全量测试
- Then 001/002/003 的身份、权限、schema 校验、非法不写回测试仍通过。

## 9. 范围

**包含：**

- 偏好提交服务：锁、revision、读取失败关闭、冲突重试、结构化结果。
- schema v2（`revision` 字段）与 v0/v1→v2 迁移。
- 自动中间件改用提交服务；成功/失败结果可判定。
- 显式 `save_user_preferences` 工具与 Agent 提示词/用户反馈约束。
- 阻止通用 `write_file`/`edit_file` 绕过偏好提交。
- 并发、异常、冲突、迁移测试。

**不包含（Out of Scope）：**

- 修改 LangGraph Store 内部实现或实现真正的 Redis WATCH/MULTI CAS（除非评审要求）。
- 跨 Redis 集群/多地域一致性、跨用户锁。
- 偏好冲突的可视化合并 UI。
- 邮件/通知类“保存失败”外部告警。

## 10. 风险与待确认

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| Q-1 | 采用 Redis 互斥锁 + revision，还是必须实现 Store 层 CAS？ | **已确认**：Redis 互斥锁 + revision（CAS 等价机制） |
| Q-2 | 锁等待/TTL/重试参数 | **已确认**：锁 TTL 10s、等待 2s、冲突重试 3 次，可配置 |
| Q-3 | 显式工具名称与参数 | **已确认**：`save_user_preferences`，只允许 4 个显式字段 |
| Q-4 | 是否阻断通用 `write_file`/`edit_file` 写偏好 | **已确认**：阻断，统一走专用工具 |
| Q-5 | schema_version 升到 2 还是仅加 `revision` 不升版本 | **已确认**：升到 2，新增 `revision` |

**风险：**

- 锁 TTL 过短可能导致长写入期间锁被抢占；需在测试中验证 TTL 大于正常提交耗时。
- Redis 不可用时偏好提交失败；自动记忆降级，显式保存必须报“未保存”。
- 阻断通用写入可能影响现有 Agent 提示词/技能，需要同步更新并回归。
- 旧 v1 文件首次提交会重写为 v2；未知字段仍拒绝，不会静默丢弃。

## 11. 验收证据（2026-10-29）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 | `tests/test_preference_service.py::test_concurrent_auto_updates_keep_both`：两个并发 auto 更新最终保留两份供应商，revision=2 |
| AC-2 | `test_read_exception_does_not_write`、`test_read_error_does_not_treat_as_empty`：零写入 |
| AC-3 | `test_write_exception_returns_failure`、`test_write_result_error_returns_failure`；middleware `test_write_failure_does_not_log_updated` |
| AC-4 | `tests/test_preference_tool.py::test_tool_returns_saved_false_for_write_failure`、`test_tool_reports_real_service_write_failure` |
| AC-5 | `test_revision_conflict_remerges_latest_content`：检测 revision 变化后重读并合并 |
| AC-6 | `tests/test_memory_backend.py::test_direct_preference_write_is_blocked`；内部写入方法单独验证 |
| AC-7 | `tests/test_preference_schema.py::test_v1_is_migrated_with_revision_zero`、`test_legacy_*`、`test_future_version_is_rejected` |
| AC-8 | `tests/test_memory_middleware.py::test_write_failure_does_not_log_updated`、自动失败不抛异常 |
| AC-9 | 全量回归：`246 passed` |

### 实现摘要

- schema v2：`revision` 字段；v0/v1→v2 迁移。
- Redis 锁 `RedisPreferenceLock`：SET NX PX + token Lua 释放；内存锁用于测试。
- `PreferenceMemoryService`：读失败关闭、冲突重读重合并、结构化 `MemoryCommitResult`。
- `UserMemoryBackend` 阻断通用偏好写，提供锁内 `write_preferences_internal`。
- 主 Agent 新增 `save_user_preferences`，返回 `saved=true/false`；提示词要求明确告知用户。
- 自动中间件只调用 service；失败不输出“已更新”。
