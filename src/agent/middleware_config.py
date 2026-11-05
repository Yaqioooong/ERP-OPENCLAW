"""
子 Agent 中间件配置。

提供标准中间件的工厂函数，在创建 Agent 时注入。
"""

from deepagents.middleware.summarization import (
    create_summarization_tool_middleware,
)
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)

from agent.config import (
    FALLBACK_MODEL,
    MODEL_RETRY_INITIAL_DELAY_SECONDS,
    MODEL_RETRY_MAX_ATTEMPTS,
    MODEL_RETRY_MAX_DELAY_SECONDS,
)
from agent.middlewares.tool_resilience import ToolResilienceMiddleware
from agent.model_resilience import (
    build_model_fallback_middleware,
    build_model_retry_middleware,
)


def _model_resilience_middleware() -> list:
    """子 Agent 共用的模型 fallback + retry 组合。

    顺序很重要：fallback 在外、retry 在内。主模型先按可重试错误退避重试，
    耗尽后再切备用模型；备用模型自身也经过内层 retry。
    """
    return [
        build_model_fallback_middleware(FALLBACK_MODEL),
        build_model_retry_middleware(
            max_retries=MODEL_RETRY_MAX_ATTEMPTS,
            initial_delay=MODEL_RETRY_INITIAL_DELAY_SECONDS,
            max_delay=MODEL_RETRY_MAX_DELAY_SECONDS,
        ),
    ]


def create_analyst_middleware(model, backend) -> list:
    """
    为 procurement-analyst 子 Agent 创建中间件列表。

    包含:
    - SummarizationToolMiddleware: 阶段完成后主动压缩上下文
    - ToolResilienceMiddleware: 工具超时/异常兜底
    - ModelFallbackMiddleware / ModelRetryMiddleware: 模型重试与降级
    - ModelCallLimitMiddleware: 防止无限循环（最多 50 次模型调用）
    - ToolCallLimitMiddleware: 防止工具调用爆炸（最多 200 次）

    Args:
        model: 用于摘要生成的模型（建议用小模型如 deepseek-v4-flash）
        backend: 文件系统后端（用于摘要持久化）

    Returns:
        中间件实例列表
    """
    return [
        create_summarization_tool_middleware(model, backend),
        ToolResilienceMiddleware(),
        *_model_resilience_middleware(),
        ModelCallLimitMiddleware(run_limit=50),
        ToolCallLimitMiddleware(run_limit=200),
    ]


def create_order_middleware() -> list:
    """
    为 procurement-order 子 Agent 创建中间件列表。

    订单操作通常是简单 API 调用，不需要摘要工具，
    需要工具兜底、模型重试/降级和调用限制防止异常循环。

    Returns:
        中间件实例列表
    """
    return [
        ToolResilienceMiddleware(),
        *_model_resilience_middleware(),
        ModelCallLimitMiddleware(run_limit=20),
        ToolCallLimitMiddleware(run_limit=50),
    ]
