"""
运行时上下文注入中间件。

从 runtime.context（ProcurementContext）中提取 user_id / username，
在 Agent 启动时以 SystemMessage 形式注入到对话中。Agent 无需调用工具
即可知道当前用户身份，从而正确读写 /memories/{user_id}/preferences.md。

使用方式:
    from agent.middlewares.context_injection import ContextInjectionMiddleware
    middlewares = ContextInjectionMiddleware()
"""

from typing import Any, Dict, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime
from langgraph.typing import ContextT

from agent.log_utils import MyLogger

logger = MyLogger().get_logger()


class ContextInjectionMiddleware(AgentMiddleware):
    """将runtime.context中的user_id/username注入到system message"""

    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """同步版本，注入用户上下文SystemMessage"""
        ctx = getattr(runtime, "context", None)
        if ctx is None:
            logger.warning("ContextInjectionMiddleware: runtime.context is None, skip")
            return None
        user_id = getattr(ctx, "user_id", None)
        if user_id is None:
            logger.warning("ContextInjectionMiddleware: runtime.context has no user_id，skip")
            return None
        username = getattr(ctx, "username", None) or user_id
        logger.info(f"ContextInjectionMiddleware: 注入用户上下文 user_id={user_id}, username={username}")
        notice = (
            f"【系统上下文】\n"
            f"当前用户user_id: {user_id}\n"
            f"当前用户username: {username}\n"
            f"用户偏好文件路径: /memories/{user_id}/preferences.md\n\n"
            f"请首先使用read_file 读取上述偏好文件，了解用户偏好。\n"
            f"(recent_suppliers和recent_queries 由系统自动维护，无需手动更新)"
        )
        return {"messages": [SystemMessage(content=notice)]}

    async def abefore_agent(
            self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """异步版本：注入用户上下文 SystemMessage。"""
        # 与同步版本逻辑相同
        return self.before_agent(state, runtime)
