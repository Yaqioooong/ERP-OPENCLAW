# 规格：记忆候选生成与记忆分级

- 规格编号：`005`
- 状态：已实现（2026-11-02）
- 提出人：待填
- 日期：2026-11-02
- 相关代码：[memory_update.py:40](src/agent/middlewares/memory_update.py:40)、[memory_update.py:109](src/agent/middlewares/memory_update.py:109)
- 前置规格：`specs/003-preference-schema-versioning/spec.md`、`specs/004-preference-concurrency/spec.md`

## 1. 背景与问题

当前自动记忆把“提取”和“持久化”混在 `MemoryUpdateMiddleware` 中：

- LLM 从“用户消息 + AI 摘要”中同时提取供应商和查询摘要，模型可能把假设、否定语义或摘要中的推测写成事实；
- 供应商候选没有区分“用户明确关注”“工具返回”“模型猜测”；
- 跳过规则使用子串匹配：`"你好，帮我采购……"` 因为包含“你好”会被跳过；
- 候选项没有 `origin`、`confidence`、`source_message_id` 等来源信息，无法做分级持久化；
- 自动记忆可能触及显式偏好甚至超出记忆范围的配置。

本需求把记忆流程拆成两步：

```text
对话/工具结果 → MemoryCandidateGenerator → [MemoryCandidate...]
                                              ↓
                                  候选校验/分级/白名单
                                              ↓
                              PreferenceMemoryService（锁 + revision）
                                              ↓
                                       持久化偏好
```

## 2. 核心模型

新增唯一候选模型 `MemoryCandidate`（Pydantic）：

```python
class MemoryCandidate(BaseModel):
    memory_type: MemoryType
    value: str
    origin: Literal["explicit", "inferred"]
    source_message_id: str
    created_at: datetime
    confidence: float          # 0..1
    source: Literal["user_message", "tool_result", "explicit_tool"]  # 可选扩展
```

`MemoryType` 只允许：

```text
preferred_output
preferred_chart_type
preferred_currency
preferred_language
recent_suppliers
recent_queries
```

- 每个候选只表达一个值；多个供应商产生多个候选。
- `source_message_id` 必须来自真实消息：
  - 普通消息优先 `message.id`，没有则使用 `thread_id:index` 兜底；
  - 工具结果使用 `ToolMessage.tool_call_id`；
  - 显式工具使用 `runtime.tool_call_id`（没有则 request_id）。
- `created_at` 使用 UTC。
- 不记录原始对话正文到候选日志，只记录类型、来源、置信度、ID。

## 3. 候选生成（第一步，不持久化）

新增 `MemoryCandidateGenerator`，只负责产生候选，不调用 `PreferenceMemoryService`。

### 3.1 显式候选

显式候选来自：

1. 用户明确表达长期偏好，例如“以后都用表格”“默认用柱状图”“记住我偏好人民币”；
2. 显式工具 `save_user_preferences`（用户明确要求保存）。

规则：

- 自动识别仅覆盖高置信度、明确的长期表达；不确定的表达不自动写长期配置，交给显式工具。
- 显式候选 `origin="explicit"`，`confidence` 取高值（如 0.95–1.0）。
- 显式候选允许修改 `preferred_*` 四个字段；不允许借此修改审批、权限、安全或 recent 列表。
- 显式工具产生的候选 `source_message_id=tool_call_id`，`source="explicit_tool"`。

### 3.2 推断候选

推断候选来自：

- **查询摘要**：从最后一条有 ERP 意图的用户消息生成，`memory_type=recent_queries`，`origin="inferred"`，`confidence≈0.7`；不采用 AI 摘要作为供应商来源。
- **供应商**：只从当前轮已返回的、白名单工具的真实结构化结果中解析，例如：
  - `supplier_get`（明确单个供应商）：高置信度；
  - `part_query` / `part_by_supplier`（记录含 `supplier_name`）：中高置信度；
  - 订单/业务结果中的供应商字段：中高置信度；
  - 宽泛的 `supplier_query` 搜索结果不自动视为“关注”，默认不产生或仅低于持久化阈值。
- 禁止从 AI 摘要、模型自由文本、技能文本或未经工具校验的猜测中生成供应商候选。
- 推断候选只允许 `recent_suppliers` / `recent_queries`；不得生成 `preferred_*` 候选。
- 每个供应商候选独立携带 `source_message_id`（对应工具消息）与置信度。

### 3.3 否定语义

- 用户消息包含“不要选择/不选/排除/不考虑/别用/拒绝 A 公司”等否定表达时，若工具结果中出现 `A 公司`，生成阶段必须过滤该供应商候选。
- 否定过滤应发生在候选进入持久化之前。
- 仅出现在 AI 摘要中的供应商不产生候选（从源头避免“假设写成事实”）。

## 4. 持久化（第二步）

候选通过 `PreferenceMemoryService.apply_candidates(user_id, candidates)` 持久化，复用 004 的锁、revision、读取失败关闭、冲突重读重合并。

### 4.1 白名单与分级

| origin | 允许的 memory_type | 行为 |
| --- | --- | --- |
| explicit | `preferred_output/chart_type/currency/language` | 可修改显式长期偏好 |
| explicit | recent 字段 | 默认拒绝或仅按显式规则处理（待确认） |
| inferred | `recent_suppliers/recent_queries` | 仅追加/合并受限字段 |
| inferred | `preferred_*` | 拒绝，不写 |
| 任意 | 其他类型 | 拒绝，不写 |

- 未知 `memory_type`、非法值、confidence 越界、空 `source_message_id` → 拒绝。
- `inferred` 低于最低置信度阈值（建议 0.6）→ 丢弃。
- 同一批候选按 `(memory_type, value, origin)` 去重。
- 推断候选不能覆盖/清除任何显式偏好字段；服务层强制执行，不能只靠生成器。

### 4.2 结果

- 复用 `MemoryCommitResult`，增加候选统计：

```text
applied_count
rejected_count
rejected_reasons: {invalid_origin, invalid_type, low_confidence, ...}
```

- 写失败/锁失败/冲突仍返回 `ok=False`；显式偏好保存失败必须向用户返回“未保存”。
- 自动记忆失败可尽力而为，不阻断对话；日志不包含候选值。

## 5. 跳过策略修正

当前 `_SKIP_PATTERNS` 子串匹配会误杀正常请求。新规则：

1. 先识别 ERP 意图（供应商、采购、订单、价格、比较、分析、库存等）；
2. 纯问候/身份询问仅在消息整体是社交短句时跳过，不能用“是否包含你好”判断；
3. 只要存在 ERP 意图，即使包含“你好/在吗”，也不得跳过；
4. 纯问候、纯身份询问不产生候选；
5. “我之前的偏好是什么”不产生新候选，但也不能被误判为需要跳过整个 ERP 请求。

示例：

| 输入 | 结果 |
| --- | --- |
| `你好` | 跳过，无候选 |
| `你好，帮我比较供应商` | 不跳过，生成 inferred query 候选 |
| `你好，帮我采购……` | 不跳过 |
| `你能做什么` | 跳过，无候选 |
| `不要选择 A 公司` | 不产生 A 的推断供应商候选 |

## 6. 验收标准

### AC-1 否定语义

- Given 用户消息“不要选择 A 公司”，当前工具结果包含 A 公司
- When 生成候选并持久化
- Then 不得产生 `recent_suppliers="A 公司"` 的候选/写入；显式偏好不受影响。

### AC-2 问候 + 采购不跳过

- Given 用户消息“你好，帮我比较供应商”
- When 生成候选
- Then 不被跳过，产生 inferred `recent_queries` 候选。

### AC-3 纯问候无候选

- Given “你好”/“你能做什么”/“在吗”
- When 生成候选
- Then 候选列表为空，且不触发偏好写入。

### AC-4 origin 区分

- Given “以后都用表格” 与工具结果中的供应商
- When 生成候选
- Then 显式偏好候选 `origin=explicit`；供应商候选 `origin=inferred`。

### AC-5 推断不覆盖显式

- Given 已有 `preferred_output: table`，推断候选尝试写入 `preferred_output` 或 `recent` 合并
- When 持久化
- Then 显式字段保持不变；推断只影响 `recent_suppliers/recent_queries`。

### AC-6 工具结果优先

- Given AI 摘要提到“B 公司”，但工具结果没有 B；工具结果包含 C
- When 生成供应商候选
- Then 不产生 B；产生 C（在置信度/工具白名单内）。

### AC-7 候选字段完整

- Given 任意候选
- Then 包含 `memory_type/value/origin/source_message_id/created_at/confidence`；source message 可追溯。

### AC-8 白名单

- Given 候选 `memory_type=approval_rule` 或 inferred `preferred_output`
- When 持久化
- Then 拒绝，不写偏好文件。

### AC-9 回归

- Given 实施完成
- When 全量测试
- Then 003/004 的 schema、锁、revision、非法不写回、显式保存结果测试仍通过。

## 7. 范围

**包含：**

- `MemoryCandidate` 模型与生成器。
- 显式/推断候选分离；工具结果供应商解析；否定过滤。
- 修正跳过策略。
- 候选白名单/置信度分级与 `apply_candidates` 持久化。
- 显式工具产生 explicit 候选并复用 004 提交服务。
- 测试与日志规范。

**不包含：**

- 修改 MCP 工具返回结构或业务系统；
- 用 LLM 自由生成供应商候选；
- 新增记忆类型（审批、权限、安全规则）；
- 跨用户/跨会话候选聚合；
- 用户可编辑的记忆审核 UI。

## 8. 约束与待确认

| 编号 | 问题 | 建议 |
| --- | --- | --- |
| Q-1 | 自动显式偏好识别用规则还是 LLM？ | **已确认**：允许 LLM 判断显式偏好 |
| Q-2 | 宽泛 `supplier_query` 搜索结果是否入库？ | **已确认**：默认不入库，只取明确/结构化结果 |
| Q-3 | 推断供应商最低 confidence | **已确认**：0.6 |
| Q-4 | 显式候选是否允许写 recent 字段 | **已确认**：不允许，recent 只由推断/自动维护 |
| Q-5 | 否定过滤范围 | **已确认**：仅过滤候选，不修改工具结果或对话 |

## 9. 验收证据（2026-11-02）

| 验收标准 | 证据 |
| --- | --- |
| AC-1 否定 | `tests/test_memory_candidates.py::test_negative_supplier_is_filtered` |
| AC-2 问候+采购 | `test_greeting_plus_procurement_is_not_skipped` |
| AC-3 纯问候 | `test_pure_greeting_has_no_candidates` |
| AC-4 origin | `test_explicit_preference_uses_llm_and_explicit_origin`、`test_supplier_comes_from_tool_result_not_ai_summary` |
| AC-5 推断不覆盖显式 | `tests/test_preference_service.py::test_inferred_candidates_never_override_explicit_values` |
| AC-6 工具优先 | `test_supplier_comes_from_tool_result_not_ai_summary`、`test_broad_supplier_search_is_below_threshold` |
| AC-7 字段完整 | `test_candidate_has_required_fields` |
| AC-8 白名单 | `test_apply_candidates_enforces_origin_and_whitelist` |
| AC-9 回归 | 全量回归：`258 passed` |

### 实现摘要

- `MemoryCandidate`：类型/value/origin/source_message_id/created_at/confidence/source。
- `MemoryCandidateGenerator`：整句社交判断、LLM 显式偏好、工具结果供应商、否定过滤、置信度。
- `PreferenceMemoryService.apply_candidates`：origin/type/confidence 白名单、候选统计、复用 004 锁/revision。
- 显式工具改为生成 explicit 候选；自动中间件改为 generator + apply_candidates。
- AI 摘要不再作为供应商来源；宽泛 `supplier_query` 低于阈值不落库。
