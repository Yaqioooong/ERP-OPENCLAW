# 技术方案：记忆候选生成与记忆分级

- 对应规格：`specs/005-memory-candidates/spec.md`
- 状态：已实现（2026-11-02）

## 1. 总体设计

```text
MemoryUpdateMiddleware
  → MemoryCandidateGenerator.generate(messages, thread_id)
        ├─ 纯社交/无 ERP 意图 → []
        ├─ 显式偏好：LLM 判断 → explicit candidates
        ├─ 查询：确定性地从最后用户消息生成 inferred 候选
        └─ 供应商：解析白名单 ToolMessage → inferred 候选
  → PreferenceMemoryService.apply_candidates(user_id, candidates)
        ├─ origin/type/confidence 白名单
        ├─ explicit：只允许 preferred_* 字段
        ├─ inferred：只允许 recent_suppliers/recent_queries
        └─ 复用 004 锁 + revision + 失败关闭
```

## 2. 候选模型

新增 `src/agent/memory/candidates.py`：

```python
class MemoryCandidate(BaseModel):
    memory_type: Literal[
        "preferred_output", "preferred_chart_type",
        "preferred_currency", "preferred_language",
        "recent_suppliers", "recent_queries",
    ]
    value: str
    origin: Literal["explicit", "inferred"]
    source_message_id: str
    created_at: datetime
    confidence: float = Field(ge=0, le=1)
    source: Literal["user_message", "tool_result", "explicit_tool"]
```

- 每个候选一个值；供应商/查询各产生一个候选。
- `value` 长度上限 500；空值拒绝。
- `source_message_id` 为空拒绝。
- `created_at` 默认 UTC，可在测试注入。

## 3. 候选生成器

新增 `src/agent/memory/candidate_generator.py`。

### 3.1 输入与消息窗口

```python
generate(
    messages: Sequence[BaseMessage],
    *,
    thread_id: str,
    request_id: str,
) -> list[MemoryCandidate]
```

- 找到最后一条 human 消息；没有则返回 `[]`。
- 仅使用最后一条 human 及其后的 AI/Tool 消息作为当前轮窗口，避免把历史轮工具结果重复计入。
- `source_message_id`：
  - 普通消息：`message.id`，缺失用 `f"{thread_id}:{index}"`；
  - 工具消息：`ToolMessage.tool_call_id`，缺失用 message id/索引。

### 3.2 跳过策略

- 先判断 ERP 意图（供应商、采购、订单、价格、比较、分析、库存等）。
- 纯社交/身份询问使用**整句**匹配，不做子串匹配：
  - `你好`、`在吗`、`你能做什么`、`我之前的偏好` 等；
  - 只在这些句子没有 ERP 意图时跳过。
- 有 ERP 意图时，`你好，帮我比较供应商` 必须继续。
- 纯问候返回空候选。

### 3.3 显式候选（LLM）

- 仅当用户消息包含显式长期表达标记（`以后`、`默认`、`记住`、`偏好`、`一直`、`长期` 等）时调用 LLM。
- Prompt 只接受 `preferred_output/chart_type/currency/language`，返回 JSON `{candidates: [{memory_type, value, confidence}]}`。
- 生成器对返回值再做白名单/枚举校验；非法值丢弃并记录计数。
- `origin="explicit"`，`confidence` 取 LLM 值（clamp 0..1，最低 0.8），`source="user_message"`。
- LLM 失败/超时 → 不产生显式候选，不影响自动记忆；显式工具仍是可靠路径。

### 3.4 推断查询

- 仅当最后用户消息有 ERP 意图且不是纯社交时生成。
- 值取用户消息规范化文本，截断 200 字符；`memory_type="recent_queries"`，`confidence=0.7`，`origin="inferred"`，`source="user_message"`。
- 不调用 LLM 生成查询摘要。

### 3.5 推断供应商

白名单（工具名 → 置信度）：

| 工具 | 置信度 | 提取字段 |
| --- | --- | --- |
| `supplier_get` | 0.95 | `name` / `supplier_name` |
| `part_query` / `part_by_supplier` / `part_get` | 0.80 | `supplier_name` / `supplierName` |
| `order_get` / `order_get_by_number` / `order_search_details` / `order_create` / `order_update` | 0.85 | `supplier_name` / `supplierName` |
| `supplier_query` / `part_search` | 0.50（低于阈值，默认不入库） | 仅结构化记录，作低置信候选 |

- 解析 `ToolMessage.content`：
  - 字符串 JSON → dict/list；
  - `{"ok": true, "data": ...}` 只取 `ok=true`；
  - 递归提取白名单字段；去重、上限 5。
- 不使用 AI 摘要/助手文本作为供应商来源。
- 否定过滤：用户消息中与供应商名相邻的 `不要/不选/排除/不考虑/别用/拒绝/除了` 等模式 → 丢弃该供应商候选。
- `origin="inferred"`，`source="tool_result"`，`confidence` 按工具表；低于 0.6 的候选不进入持久化。
- 每个候选的 `source_message_id` 为该工具消息的 `tool_call_id`。

## 4. 持久化扩展

`src/agent/memory/preference_service.py` 新增：

```python
async def apply_candidates(
    self, user_id: str, candidates: Sequence[MemoryCandidate],
    *, request_id: str = "", thread_id: str = "",
) -> MemoryCommitResult
```

- 先做白名单/分级：
  - explicit：仅 `preferred_*`；写 recent → `invalid_origin`；
  - inferred：仅 `recent_*`；写 preferred → `invalid_origin`；
  - unknown type/空 source/confidence 越界 → 拒绝；
  - inferred `confidence < 0.6` → `low_confidence`；
  - 同批 `(type,value,origin)` 去重。
- 构造 mutation：
  - explicit 候选按最高 confidence 覆盖同字段；显式字段写入并校验；
  - inferred recent_suppliers/queries 合并、去重、上限 10/5；
  - 显式字段永远不会被 inferred 修改。
- 复用 `_commit`（锁/revision/冲突/读失败关闭）。
- 结果扩展：
  - `applied_count`
  - `rejected_count`
  - `rejected_reasons`
  - 失败仍 `ok=False`，显式保存返回 `saved=false`。
- `save_explicit` 改为构造 explicit `MemoryCandidate` 后调用 `apply_candidates`，使用 `runtime.tool_call_id` 作为 source。
- `update_auto` 保留为兼容包装（把 suppliers/query 转为 inferred 候选），生产中间件改用 `apply_candidates`。

## 5. 中间件与工具

### 5.1 MemoryUpdateMiddleware

```python
MemoryUpdateMiddleware(
    model=SUMMARY_MODEL,
    generator=MemoryCandidateGenerator(SUMMARY_MODEL),
    preference_service=service,
)
```

- 删除 `_extract_entities` / `_extract_ai_summary` / 旧跳过规则。
- 流程：
  1. 身份校验；
  2. `candidates = await generator.generate(messages, thread_id=..., request_id=...)`；
  3. 空 → noop；
  4. `result = await service.apply_candidates(...)`；
  5. 成功/失败按 004 规则记录；日志只记录候选数量、类型计数、origin 计数，不记录 value。
- 自动失败仍不阻断对话。

### 5.2 save_user_preferences

- 输入不变（4 个显式字段的 updates）。
- 生成 explicit 候选（confidence=1.0，source_message_id=runtime.tool_call_id 或 request_id），调用 `apply_candidates`。
- 结果仍返回 `saved=true/false`、revision；失败原因沿用 004 映射。

## 6. 文件清单

| 文件 | 改动 |
| --- | --- |
| `src/agent/memory/candidates.py` | 新增候选模型 |
| `src/agent/memory/candidate_generator.py` | 新增生成器、工具结果解析、否定过滤、LLM 显式判断 |
| `src/agent/memory/preference_service.py` | 新增 `apply_candidates`、候选统计；`save_explicit` 走候选 |
| `src/agent/middlewares/memory_update.py` | 删除旧提取/跳过，改用生成器+apply_candidates |
| `src/agent/tools/preference_tools.py` | 传递 tool_call_id/source，构造 explicit 候选 |
| `src/agent/main_agent.py` | 装配 generator，中间件参数 |
| `src/agent/memory/AGENTS.md` / `prompts.py` | 说明候选来源与显式工具 |
| `tests/test_memory_candidates.py` | 新增生成器/否定/跳过/工具结果测试 |
| `tests/test_preference_service.py` | 新增分级/白名单/推断不覆盖显式测试 |
| `tests/test_memory_middleware.py` | 适配新流程 |
| `tests/test_preference_tool.py` | explicit 候选结果 |

## 7. 测试策略

| 验收 | 测试 |
| --- | --- |
| AC-1 否定 | `test_negative_supplier_is_filtered`：消息“不要选择 A 公司”+工具结果 A → 无 A |
| AC-2 问候+采购 | `test_greeting_plus_procurement_not_skipped`：产生 query 候选 |
| AC-3 纯问候 | `test_pure_greeting_has_no_candidates` |
| AC-4 origin | `test_explicit_and_inferred_origins`：表格/供应商候选 origin 不同 |
| AC-5 推断不覆盖显式 | `test_inferred_cannot_override_explicit` |
| AC-6 工具优先 | `test_supplier_only_from_tool_result_not_ai_summary` |
| AC-7 字段完整 | `test_candidate_required_fields` |
| AC-8 白名单 | `test_apply_rejects_security_types_and_inferred_preferred` |
| AC-9 回归 | 全量 pytest |

额外：
- 工具结果解析：`supplier_get`、`part_query`、`order_get`；`ok=false` 忽略；
- 宽泛 `supplier_query` 低置信不落库；
- 显式 LLM 返回非法枚举 → 丢弃；
- 候选统计 applied/rejected。

## 8. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| LLM 显式判断误报 | 仅显式标记触发；白名单+枚举校验；显式工具为可靠路径 |
| 工具结果格式变化 | 解析器只依赖白名单字段，失败降级为空候选并记录类型 |
| 否定过滤误杀/漏杀 | 规则保守；仅过滤明确否定邻近供应商 |
| 候选过多/噪音 | 置信度阈值、去重、上限 5/10/5 |
| 旧 004 中间件测试依赖旧提取 | 更新测试与构造器 |
| 显式候选覆盖 recent | 服务层拒绝，不依赖生成器 |

回滚：删除候选模块并恢复旧中间件提取；schema/revision 不回滚。

## 9. 实施顺序

1. 候选模型 + 生成器（跳过、显式 LLM、工具解析、否定）。
2. `apply_candidates` 分级/白名单/统计 + 显式工具改造。
3. 中间件/main_agent 切换。
4. 测试补齐、全量回归、文档/证据。


## 10. 实施结果（2026-11-02）

- 新增候选模型与生成器；显式偏好 LLM 判断，供应商只来自白名单工具结果，否定过滤生效。
- `apply_candidates` 实现 origin/type/confidence 白名单、统计、推断不覆盖显式；复用 004 提交服务。
- `save_user_preferences` 改为 explicit 候选路径；`update_auto` 兼容包装也走候选白名单。
- 中间件改为 generator + apply_candidates；删除 AI 摘要提取供应商与旧子串跳过规则。
- 验证：`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → **258 passed**。
- 与方案偏差：无；工具参数仍为 `updates` 字典，便于区分未提供字段与显式 null。
