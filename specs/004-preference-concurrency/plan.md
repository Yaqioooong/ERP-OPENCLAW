# 技术方案：偏好记忆并发安全与可判定写入结果

- 对应规格：`specs/004-preference-concurrency/spec.md`
- 状态：已实现（2026-10-29）
- 作者：待填
- 日期：2026-10-29

## 1. 总体设计

```text
MemoryUpdateMiddleware（自动 recent）
  └─ PreferenceMemoryService.update_auto(user_id, suppliers, query)
        └─ Redis 互斥锁（per user）
             ├─ 读取偏好 + revision（读取异常→失败关闭）
             ├─ 模型合并（只改 recent；显式字段不动）
             ├─ 写前再次读取 revision
             │    ├─ 变化 → 重新读取/合并（最多 3 次）
             │    └─ 未变化 → revision+1，写回
             └─ MemoryCommitResult(ok, revision, reason)

Agent 工具 save_user_preferences（显式 preferred_*）
  └─ PreferenceMemoryService.save_explicit(...)
        └─ 同一把锁、同一 revision 机制
             └─ 返回 saved=true/false，Agent 必须明确告知用户
```

关键点：

- **所有偏好写入只能经过 `PreferenceMemoryService`**；通用 `write_file` / `edit_file` 写 `preferences.md` 直接拒绝。
- 锁是跨实例的 Redis 锁，不是进程内 `asyncio.Lock`。
- `revision` 是内容版本；`schema_version` 是格式版本，两者独立。
- 失败不产生成功结果、不写“已更新”日志；显式保存失败必须让用户看到。

## 2. schema v2 与迁移

### 2.1 模型变化

`src/agent/memory/preferences.py`：

```python
CURRENT_SCHEMA_VERSION = 2

class UserPreferences(BaseModel):
    schema_version: int = 2
    revision: int = 0
    preferred_output: ...
    ...
```

- `revision`：非负整数；读取时缺省 0；每次成功写入 `revision = 旧值 + 1`。
- `schema_version`：只接受当前 v2；v0/v1 在加载阶段迁移后校验。
- 新增字段不改变 003 的 strict/extra=forbid/枚举/列表规则。

### 2.2 迁移规则

| 输入 | 结果 |
| --- | --- |
| 无 `schema_version` 或 `0` | 迁移为 v2，`revision: 0`，保留显式字段，`migrated=True` |
| `schema_version: 1` | 迁移为 v2，`revision: 0`，保留显式字段，`migrated=True` |
| `schema_version: 2` | 当前格式，`revision` 缺省 0 |
| `schema_version > 2` | `unsupported_version`，拒绝写入 |
| 未知字段/非法枚举/类型错误 | 拒绝写入（沿用 003） |

- 迁移只在读取时完成，不单独写回；首次成功提交写回 v2。
- `LoadedPreferences` 增加 `migrated` 语义保持不变。

## 3. 锁组件

新增 `src/agent/memory/preference_lock.py`。

### 3.1 接口

```python
class PreferenceLock(Protocol):
    async def acquire(self, key: str, *, ttl_ms: int, wait_ms: int) -> str | None: ...
    async def release(self, key: str, token: str) -> None: ...
```

`None` 表示等待超时；Redis 连接异常由实现抛 `PreferenceLockUnavailable`。

### 3.2 Redis 实现

- Key：`erp:memory:lock:{user_id}:preferences`
- 获取：`SET key token NX PX ttl_ms`
- token：`uuid4().hex`，释放时用 Lua 校验 token 后再 `DEL`，避免误删他人锁
- 等待：每 50ms 重试，总等待 `wait_ms`；超时返回 `None`
- 异常：Redis 不可用 → `PreferenceLockUnavailable`
- 配置（环境变量，带默认值）：

| 变量 | 默认值 |
| --- | --- |
| `PREFERENCE_LOCK_TTL_MS` | `10000` |
| `PREFERENCE_LOCK_WAIT_MS` | `2000` |
| `PREFERENCE_CONFLICT_RETRIES` | `3` |
| `PREFERENCE_LOCK_RETRY_MS` | `50` |

同步 `REDIS_CLIENT` 的调用通过 `asyncio.to_thread` 执行，避免阻塞事件循环。

## 4. 提交服务

新增 `src/agent/memory/preference_service.py`。

### 4.1 结果契约

```python
@dataclass(frozen=True)
class MemoryCommitResult:
    ok: bool
    revision: int | None
    reason: str
    retryable: bool = False
    changed: bool = False
    message: str = ""
```

`reason` 取值：

```text
ok | noop | read_failed | invalid_schema | write_failed
| lock_timeout | lock_unavailable | conflict | invalid_request
| permission_denied
```

### 4.2 API

```python
class PreferenceMemoryService:
    def __init__(
        self,
        backend: UserMemoryBackend,
        lock: PreferenceLock,
        *,
        retries: int = 3,
    ): ...

    async def update_auto(
        self, user_id: str, suppliers: list[str], query: str,
        *, request_id: str = "", thread_id: str = "",
    ) -> MemoryCommitResult: ...

    async def save_explicit(
        self, user_id: str, updates: Mapping[str, Any],
        *, request_id: str = "", thread_id: str = "",
    ) -> MemoryCommitResult: ...
```

### 4.3 提交流程

```text
acquire lock
  ├─ 失败：lock_timeout / lock_unavailable（不写）
  └─ 成功：
       for attempt in 0..retries:
         read1 = backend.aread(preferences_key(user_id))
         ├─ not found → 默认模型（唯一允许的空文件来源）
         ├─ 其他 error → read_failed（不写）
         └─ 解析失败 → invalid_schema（不写）
         应用 mutation（auto / explicit）
         若没有变化且未迁移 → noop（不写）
         read2 = backend.aread(...)
         ├─ error → read_failed
         └─ revision(read2) != revision(read1) → 继续下一轮（重新读取+合并）
         candidate.revision = read2.revision + 1
         write = backend.write_preferences_internal(user_id, path, dump(candidate))
         ├─ 异常/error → write_failed（不写成功日志）
         └─ 成功 → ok=True, revision=candidate.revision
       重试耗尽 → conflict
release lock (Lua token check)
```

- 读失败分支绝不创建默认文件、绝不调用写。
- 只有 `ok=True` 且 `changed=True` 才允许发布“已更新”日志/成功工具结果。
- `noop` 返回 `ok=True, changed=False, revision=当前值`，不写文件。
- 迁移（v0/v1→v2）即使没有新 auto 字段也视为 `changed=True`，写回一次 v2。
- `write_failed` 包含 `aput` 抛异常、`WriteResult.error`、内部写方法抛异常。

### 4.4 自动与显式 mutation

**auto**：`merge_auto_fields(current, suppliers, query)`，只改 `recent_suppliers` / `recent_queries`，保留显式字段与 `revision`（revision 由提交层设置）。

**explicit**：

- 允许键仅：`preferred_output`、`preferred_chart_type`、`preferred_currency`、`preferred_language`。
- 未提供的字段保持不变；显式 `None` 表示清除。
- 禁止 `recent_*` 和未知键；非法 → `invalid_request`，不写。
- 以当前模型 `model_dump` 为基础更新字段，保留 recent 列表与 schema/revision 语义。

## 5. Backend 防绕过

修改 `src/agent/memory/user_memory_backend.py`：

- 识别偏好路径（`preferences.md`）。
- 通用 **write/awrite/edit/aedit/delete/adelete/upload_files** 对偏好路径返回：

```text
use_save_preferences_tool
```

- 读取仍允许。
- 新增内部方法供提交服务使用：

```python
async def write_preferences_internal(
    self, user_id: str, file_path: str, content: str
) -> WriteResult:
    ...
```

  该方法校验 `user_id` 与当前 runtime 用户一致、路径属于该用户，再调用 `StoreBackend.awrite`；保留 001 的路径校验与审计日志。
- 这样即使 Agent 误用 `write_file`，也无法绕过锁与 revision；错误会回传给 Agent，提示改用专用工具。

迁移影响：001 的 Backend 测试原先直接 `awrite("/u_a/preferences.md", ...)`，需要改为使用内部方法（或非偏好路径）以验证普通记忆写入。

## 6. 显式保存工具

新增 `src/agent/tools/preference_tools.py`。

### 6.1 输入

```python
class SaveUserPreferencesInput(BaseModel):
    preferred_output: Literal["table", "chart"] | None = None
    preferred_chart_type: ChartType | None = None
    preferred_currency: Currency | None = None
    preferred_language: Language | None = None
```

- 通过 `model_dump(exclude_unset=True)` 区分“未提供”和“置空”。
- 至少提供一个字段，否则返回 `invalid_request`。

### 6.2 输出

```json
{"saved": true, "revision": 3, "changed": true}
```

或

```json
{"saved": false, "reason": "write_failed", "retryable": true,
 "message": "偏好未保存，请稍后重试"}
```

- `message` 由 reason 映射，禁止包含偏好正文/供应商名。
- 工具 description 明确要求 Agent 必须根据 `saved` 告知用户“已保存/未保存”。
- `build_preference_tools(service)` 返回 `StructuredTool`，由 `main_agent` 加入主 Agent 工具池（不加入子 Agent）。

## 7. 中间件改造

`src/agent/middlewares/memory_update.py`：

- 构造函数改为：

```python
MemoryUpdateMiddleware(model=SUMMARY_MODEL, preference_service=service)
```

- 保留 `_is_meaningful_erp_exchange` / `_extract_entities`。
- 不再直接 `aread` / `awrite`；调用 `update_auto`。
- 逻辑：

```python
result = await preference_service.update_auto(...)
if result.ok and result.changed:
    log "updated ... revision=..."
elif not result.ok:
    log "write failed ... reason=... retryable=..."
# 其他情况（noop）不输出“已更新”
```

- 任何异常由 middlewares 捕获为 best-effort，不影响本轮回复；日志不含正文。
- 删除旧的手工读取/合并逻辑（003 已模型化，此处进一步收敛到 service）。

## 8. main_agent 装配

`src/agent/main_agent.py`：

```python
user_memory_backend = UserMemoryBackend(store=STORE, namespace=user_namespace_for_runtime)
backend = CompositeBackend(
    default=sandbox_backend,
    routes={
        "/memories/": user_memory_backend,
        "/persisted-skills/": StoreBackend(...),
    },
)
preference_service = PreferenceMemoryService(
    backend=user_memory_backend,
    lock=RedisPreferenceLock(REDIS_CLIENT),
)
```

- `MemoryUpdateMiddleware(model=SUMMARY_MODEL, preference_service=preference_service)`
- 工具池新增 `build_preference_tools(preference_service)`。
- `save_user_preferences` 只在主 Agent 暴露；子 Agent 不持有偏好写权限。

## 9. 文件清单

| 文件 | 改动 |
| --- | --- |
| `src/agent/memory/preferences.py` | schema v2、`revision`、v0/v1→v2 迁移 |
| `src/agent/memory/preference_lock.py` | 新增 Redis 锁协议与实现 |
| `src/agent/memory/preference_service.py` | 新增提交服务、结果契约、冲突重试 |
| `src/agent/memory/user_memory_backend.py` | 阻断通用偏好写入、内部写方法 |
| `src/agent/tools/preference_tools.py` | 新增 `save_user_preferences` 工具 |
| `src/agent/middlewares/memory_update.py` | 改用提交服务，失败不误报 |
| `src/agent/main_agent.py` | 装配 service/工具/中间件 |
| `src/agent/memory/AGENTS.md` | 显式偏好必须用专用工具；默认 schema v2 |
| `src/agent/memory/prompts.py` | 更新默认文件/显式保存指令 |
| `tests/test_preference_schema.py` | 更新 v2/revision/迁移断言 |
| `tests/test_preference_service.py` | 新增并发/读失败/写失败/冲突/显式测试 |
| `tests/test_preference_tool.py` | 新增工具 saved 成功/失败结果 |
| `tests/test_memory_backend.py` | 直写偏好被拒、内部写可用 |
| `tests/test_memory_middleware.py` | 适配新构造与结果语义 |
| `tests/fakes.py` | 如需，增加锁/Store 故障注入能力 |

## 10. 测试策略

| 验收标准 | 测试 |
| --- | --- |
| AC-1 并发不同变更 | `test_preference_service.py::test_concurrent_auto_updates_keep_both`：`asyncio.gather` 两次不同供应商，最终都保留，revision=2 |
| AC-2 读取异常不写 | `test_read_failure_does_not_write`：`aread` 抛异常/返回非 not-found，断言 `write_preferences_internal` 未调用 |
| AC-3 写入异常不误报 | `test_write_failure_returns_false`、middleware 日志断言无 “updated”；工具返回 `saved=false` |
| AC-4 显式结果 | `test_preference_tool.py::test_save_explicit_success/failure`：JSON `saved` 字段 |
| AC-5 冲突重读合并 | `test_revision_conflict_remerges`：注入“读取后 revision 变化”的 backend，断言第二次读取并合并 |
| AC-6 绕过阻断 | `test_direct_preference_write_is_blocked`：`awrite/edit` 返回 `use_save_preferences_tool` |
| AC-7 迁移 | `test_v1_to_v2_migration`、`test_v0_to_v2_migration`、`test_future_version_rejected` |
| AC-8 自动失败不阻断 | middleware 捕获 `ok=False`，对话流程不抛异常 |
| AC-9 回归 | 全量 pytest |

并发测试实现：

- 使用 `InMemoryStore` + 固定 namespace 的 `UserMemoryBackend`。
- 使用测试用 `InMemoryPreferenceLock`（`asyncio.Lock` 语义）验证 service 串行化；另用 FakeRedis 单测 `RedisPreferenceLock` 的 NX/TTL/Lua token 释放。
- 故障注入：`FailingReadBackend`、`FailingWriteBackend`、`RevisionChangingBackend`。

## 11. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 锁 TTL 小于提交耗时，锁被抢占 | TTL 10s 远大于正常读写；测试覆盖；可通过环境变量调整 |
| Redis 不可用导致自动记忆全部失败 | 自动尽力而为；显式保存返回 `lock_unavailable`，Agent 告知未保存；不影响聊天主流程 |
| 阻断 `write_file` 破坏旧提示词/技能 | 同步更新 AGENTS/prompts；工具返回明确错误；回归 Agent 提示词相关测试 |
| 旧 v1 文件首次提交重写 | 迁移规则保留显式字段；未知字段仍拒绝；先在测试库验证 |
| 直接写 Store 绕过锁 | 产品代码统一走 Backend/Service；Backend 阻断偏好直写；文档标注 |
| 锁实现依赖同步 Redis 阻塞事件循环 | `asyncio.to_thread` 包装；锁等待使用异步 sleep |

回滚：

- 代码回滚到 003 版本；v2 文件仍可被 003 读取吗？003 模型不认识 `revision` 会因 `extra=forbid` 拒绝。因此回滚前需将文件降级/删除 `revision`，或保留 004 代码。发布说明中写明 schema 不可逆点。
- 锁 key 会随 TTL 自动释放；无持久数据迁移。

## 12. 实施顺序

1. `preferences.py` v2 + revision + 迁移，更新 003 测试。
2. `preference_lock.py`、`preference_service.py` + 并发/故障测试。
3. `UserMemoryBackend` 阻断/内部写 + 工具。
4. 中间件、main_agent、提示词切换。
5. 工具结果测试、全量回归、文档与证据。

## 13. 待评审确认

- 本文的锁/服务边界、schema v2、阻断通用写入、显式工具结果格式是否接受。
- 锁参数按规格：TTL 10s、等待 2s、冲突重试 3 次。
- 回滚不可逆点（v2 文件不能被 003 代码读取）是否接受；若不接受，需要回滚前降级脚本或让 003 兼容 `revision`。


## 14. 实施结果（2026-10-29）

- 已实现 Redis 互斥锁 + revision schema v2；v0/v1 文件可迁移，未知字段/非法结构继续拒绝。
- 自动记忆与显式偏好共用 `PreferenceMemoryService`；读失败、写失败、锁超时、冲突均有结构化结果。
- `UserMemoryBackend` 阻断 preferences 直写，`write_preferences_internal` 仅服务内部使用。
- 主 Agent 新增 `save_user_preferences`；工具返回 `saved=true/false`，失败原因可展示。
- 自动中间件失败不会输出“已更新”，显式失败不会返回成功。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → **246 passed**。
- 与方案偏差：无；工具参数采用 `updates` 字典以准确保留“未提供字段”语义。
