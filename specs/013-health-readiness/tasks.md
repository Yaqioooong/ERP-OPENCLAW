# 任务：启动降级与健康检查（013）

- 规格：`specs/013-health-readiness/spec.md`
- 计划：`specs/013-health-readiness/plan.md`

## 任务清单

- [x] 1. `src/agent/config.py` 删除模块级 `STORE.setup()`，新增 `initialize_redis_store()`。验证：静态断言。
- [x] 2. `AgentLoader` 调用 lazy store，增加 `is_ready`/`last_error`，失败记录脱敏错误，close 清理 agent。验证：单测。
- [x] 3. 新增 `src/api_view/health.py`：checks、缓存、超时、脱敏、`/live`、`/ready`、`/health`。验证：health 单测。
- [x] 4. `web_main.py` 改为后台初始化 + startup_errors；注册 health router；shutdown 等待/取消 init task。验证：静态断言 + 单测。
- [x] 5. `chat.py` 未就绪返回 503 `AGENT_NOT_READY`。验证：单测。
- [x] 6. `dependencies.py` 增加 task runner / worker 就绪查询。验证：静态断言。
- [x] 7. `start_web.py` 改为等待 `/ready` 并打印 checks。验证：静态断言。
- [x] 8. 新增 `tests/test_health.py`，覆盖 live/ready/health、降级、超时、缓存、脱敏、未就绪聊天。验证：目标测试通过。
- [x] 9. 更新 README；跑目标回归；回填 spec 验收证据。验证：测试输出 + 文档。
