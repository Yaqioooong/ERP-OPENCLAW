"""
自动记忆更新中间件。

在每轮 Agent 回复完成后（aafter_agent 钩子），自动提取对话中涉及的
供应商名称和查询摘要，更新 StoreBackend 中的用户偏好文件。

Agent 无需手动维护 recent_suppliers / recent_queries —— 系统自动处理。

使用方式:
    from agent.middlewares.memory_update import MemoryUpdateMiddleware
    middlewares = MemoryUpdateMiddleware(model=SUMMARY_MODEL, backend=backend)
"""
import json
from typing import List, Optional, Dict, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from agent.log_utils import MyLogger
from agent.memory.audit import log_memory_event
from agent.memory.identity import (
    MemoryIdentityError,
    preferences_path,
    require_user_id,
)
from agent.memory.preferences import (
    PreferenceSchemaError,
    default_preferences,
    dump_preferences,
    load_preferences,
    merge_auto_fields,
)

logger = MyLogger().get_logger()
# 触发自动更新的 ERP 业务关键词（中文）
_TRIGGER_KEYWORDS = [
    "供应商", "物料", "采购", "订单", "价格", "分析", "对比",
    "比价", "报价", "评估", "筛选", "推荐", "行情", "预算",
    "库存", "交货", "交期", "质量", "成本", "报价", "招标",
    "supplier", "part", "order", "price", "analysis",
]

# 跳过更新的无意义消息模式
_SKIP_PATTERNS = [
    "你好", "在吗", "嗨", "hello", "hi", "hey",
    "你能做什么", "你有哪些功能", "你是谁",
    "我之前的偏好", "我的偏好", "我的记忆",
]


def _is_meaningful_erp_exchange(messages: List[BaseMessage]) -> Optional[str]:
    """检查最后一条用户消息是否为有意义的 ERP 交互

    Returns:
        用户消息文本（有意义时），或为 None （应跳过）
    """
    last_user_msg = None
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            last_user_msg = msg
            break

    if last_user_msg is None:
        return None

    content = last_user_msg.content
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    content = str(content).strip()

    if not content:
        return None

    # 跳过无意义消息
    content_lower = content.lower().replace(" ", "")
    for pattern in _SKIP_PATTERNS:
        if pattern.lower().replace(" ", "") in content_lower:
            return None

    # 检查是否包含 ERP 关键词
    has_erp_keyword = any(
        kw.lower() in content_lower for kw in _TRIGGER_KEYWORDS
    )

    if not has_erp_keyword:
        # 兜底：检查是否委派了子Agent(messages中有task工具调用)
        has_subagent_call = False
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.get("name") == "task":
                        has_subagent_call = True

            if has_subagent_call:
                break
        if not has_subagent_call:
            return None
    return content


def _extract_ai_summary(messages: List[BaseMessage]) -> str:
    """提取最后一条AI消息的前300字作为摘要"""
    for msg in reversed(messages):
        if getattr(msg,"type",None) == "ai":
            content = msg.content
            if isinstance(content,list):
                content = " ".join(
                    part.get("text","") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return str(content)[:300]
    return ""


async def _extract_entities(
        model: BaseChatModel, user_message: str, ai_summary: str
) -> Dict[str, Any]:
    """
    使用LLM 从催化中提取供应商和查询摘要

    Returns:
        {"suppliers": [...], "query": "..."} 或 {"suppliers": [], "query": ""}
    """

    prompt = f"""Extract procurement-related entities from this conversation.

Rules:
1. "suppliers": Company/supplier names mentioned. Include both Chinese and English names. Empty list if none.
2. "query": One-line summary of the user's procurement need. Empty string if not procurement-related.

User message: {user_message}

Assistant response summary: {ai_summary}

Return ONLY a JSON object, no other text:
{{"suppliers": ["CompanyA", "CompanyB"], "query": "brief summary"}}"""

    try:
        response = await model.ainvoke(prompt)
        # 从回复中提取json
        text = response.content
        if isinstance(text, list):
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            )
        text = str(text).strip()

        # 提取JSON块
        start = text.find("{")
        end = text.find("}")
        if start != -1 and end != -1 and end > start:
            result = json.loads(text[start:end + 1])
            return {
                "suppliers": result.get("suppliers", []),
                "query": result.get("query", ""),
            }
    except Exception:
        logger.warning("MemoryUpdateMiddleware: LLM 提取失败，跳过本次更新", exc_info=True)

    return {"suppliers": [], "query": ""}

class MemoryUpdateMiddleware(AgentMiddleware):
    """在 Agent 回复后自动更新用户记忆文件中的 recent_suppliers / recent_queries。

    所有读写都经过 CompositeBackend → UserMemoryBackend，确保命名空间、
    路径校验与审计日志只有一条入口。身份缺失时拒绝写入，不回退默认用户。
    """

    def __init__(self, model: BaseChatModel, backend: Any) -> None:
        super().__init__()
        self.model = model
        self.backend = backend

    # ---- 同步钩子（不执行操作）----
    def after_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        return None

    # ---- 异步钩子（核心逻辑）----
    async def aafter_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """Agent 回复完成后触发：提取实体并更新记忆。"""
        ctx = getattr(runtime, "context", None)
        request_id = str(getattr(ctx, "request_id", "") or "")
        thread_id = str(getattr(ctx, "thread_id", "") or "")

        try:
            user_id = require_user_id(ctx)
        except MemoryIdentityError:
            # 失败关闭：不读取、不写入任何 Store。
            logger.error(
                "MemoryUpdateMiddleware: missing trusted user_id, "
                "refuse memory write request_id={} thread_id={}",
                request_id,
                thread_id,
            )
            return None

        try:
            messages: List[BaseMessage] = state.get("messages", [])
            if not messages:
                return None

            user_message = _is_meaningful_erp_exchange(messages)
            if user_message is None:
                return None

            ai_summary = _extract_ai_summary(messages)
            extracted = await _extract_entities(self.model, user_message, ai_summary)
            suppliers = extracted.get("suppliers", [])
            query = extracted.get("query", "")

            # 先解析现有文件，再决定是否更新；非法文件必须保持原样。
            path = preferences_path(user_id)
            read_result = await self.backend.aread(path)

            if read_result.error:
                if "not found" not in read_result.error.lower():
                    logger.warning(
                        "MemoryUpdateMiddleware: read failed user={} reason={}",
                        user_id,
                        read_result.error,
                    )
                    return None
                preferences = default_preferences()
                migrated = False
            else:
                file_data = read_result.file_data or {}
                content: Any = (
                    file_data.get("content", "")
                    if isinstance(file_data, dict)
                    else getattr(file_data, "content", "")
                )
                if isinstance(content, list):
                    text = "\n".join(str(line) for line in content)
                else:
                    text = str(content or "")
                try:
                    loaded = load_preferences(text)
                except PreferenceSchemaError as exc:
                    # 记录原因和字段名，不记录 YAML 正文；绝不写回。
                    logger.warning(
                        "MemoryUpdateMiddleware: invalid preferences user={} "
                        "request_id={} thread_id={} reason={} fields={}",
                        user_id,
                        request_id,
                        thread_id,
                        exc.reason,
                        sorted(exc.field_errors),
                    )
                    log_memory_event(
                        op="memory.preferences_validate",
                        user_id=user_id,
                        namespace=(user_id,),
                        path=path,
                        request_id=request_id,
                        thread_id=thread_id,
                        outcome="denied",
                        reason=exc.reason,
                    )
                    return None
                preferences = loaded.preferences
                migrated = loaded.migrated

            if not suppliers and not query and not migrated:
                return None

            merged = merge_auto_fields(preferences, suppliers, query)

            # 日志只记录计数和请求关联，不记录供应商名/查询正文。
            logger.info(
                "MemoryUpdateMiddleware: user={} suppliers_count={} "
                "query_present={} migrated={} request_id={} thread_id={}",
                user_id,
                len(suppliers),
                bool(query),
                migrated,
                request_id,
                thread_id,
            )

            updated_content = dump_preferences(merged)
            write_result = await self.backend.awrite(path, updated_content)

            if write_result.error:
                logger.warning(
                    "MemoryUpdateMiddleware: write failed user={} reason={} "
                    "request_id={}",
                    user_id,
                    write_result.error,
                    request_id,
                )
                return None

            logger.info(
                "MemoryUpdateMiddleware: updated user={} suppliers_count={} "
                "query_present={} migrated={} request_id={}",
                user_id,
                len(suppliers),
                bool(query),
                migrated,
                request_id,
            )
            log_memory_event(
                op="memory.update_preferences",
                user_id=user_id,
                namespace=(user_id,),
                path=path,
                request_id=request_id,
                thread_id=thread_id,
                outcome="ok",
            )
        except Exception as exc:
            # 不输出 exc_info：校验异常可能携带字段值/正文。
            logger.warning(
                "MemoryUpdateMiddleware: update failed user={} request_id={} "
                "error_type={}",
                locals().get("user_id", ""),
                request_id,
                type(exc).__name__,
            )

        return None
