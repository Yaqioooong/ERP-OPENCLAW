# 技术方案：记忆测试矩阵与最小指标

- 对应规格：`specs/007-memory-test-observability/spec.md`
- 状态：已实现（2026-11-02）

## 1. 指标

新增 `src/agent/memory/metrics.py`：

```python
class MemoryMetrics:
    inc(name, labels=None, value=1)
    snapshot() -> {"counters": {...}}
    reset()
```

- 线程安全、进程内、无第三方依赖。
- 失败原因使用低基数 label（如 `low_confidence`/`invalid_origin`）。
- `GET /api/memory/metrics` 仅 admin；不返回正文。

## 2. 埋点

- `PreferenceMemoryService._read`：读成功/失败、解析失败。
- `_commit`：写成功/失败、冲突。
- `apply_candidates`：候选拒绝原因计数。
- `save_explicit`：显式保存失败计数。
- `MemoryUpdateMiddleware`：自动候选总数；审计写入 `message_ids`。
- API：删除/清空审计不记录 value。

## 3. 测试

新增：

- `tests/test_memory_contract.py`：首次使用、message_id 追溯、会话/记忆边界、采购不依赖偏好、无工具不产生供应商。
- `tests/test_memory_metrics.py`：读/写/冲突/解析/候选拒绝/显式失败指标、admin 端点。
- 复用并补充既有隔离、结构、显式、自动提取、故障、并发、遗忘测试。

## 4. 验证

`PYTHONPATH=src .venv/bin/python -m pytest -o addopts="" -q` → 280 passed；前端无需改动。

## 5. 风险

- 进程内指标在多实例下不聚合；生产可替换为 Prometheus/Redis。
- 指标端点仅 admin，避免暴露运营细节。
