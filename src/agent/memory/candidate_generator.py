"""从对话/工具结果生成记忆候选；不负责持久化。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from loguru import logger
from pydantic import ValidationError

from agent.memory.candidates import (
    EXPLICIT_MEMORY_TYPES,
    MemoryCandidate,
)
from agent.memory.preferences import CURRENT_SCHEMA_VERSION, UserPreferences

_ERP_KEYWORDS = (
    "供应商", "供应", "采购", "订单", "价格", "比价", "报价", "比较",
    "对比", "分析", "评估", "筛选", "推荐", "行情", "预算", "库存",
    "物料", "交货", "交期", "质量", "成本", "招标",
    "supplier", "part", "order", "price", "compare", "analysis",
    "quotation", "inventory",
)

_EXPLICIT_MARKERS = (
    "以后", "今后", "默认", "记住", "偏好", "一直", "长期", "下次", "都用", "都用",
)

_SOCIAL_ONLY = re.compile(
    r"^(你好|您好|嗨|哈喽|在吗|早上好|下午好|晚上好|"
    r"hello|hi|hey|"
    r"你能做什么|你有哪些功能|你是谁|"
    r"我的偏好是什么|我之前的偏好是什么|我的记忆)[\s!,.。？?~]*$",
    re.IGNORECASE,
)

# 工具 → 置信度；供应商搜索结果默认低于持久化阈值。
_TOOL_CONFIDENCE = {
    "supplier_get": 0.95,
    "part_query": 0.80,
    "part_by_supplier": 0.80,
    "part_get": 0.80,
    "order_get": 0.85,
    "order_get_by_number": 0.85,
    "order_search_details": 0.85,
    "order_create": 0.85,
    "order_update": 0.85,
    "supplier_query": 0.50,
    "part_search": 0.50,
}

_NEGATION_PREFIXES = (
    "不要选择", "不要选", "不选", "排除", "不考虑", "别用", "拒绝", "除了", "不要",
)

_LLM_PROMPT = """You extract ONLY explicit long-term preference statements from the user message.

Allowed memory_type values:
- preferred_output: table or chart
- preferred_chart_type: one of area/bar/boxplot/column/dual_axes/funnel/histogram/line/liquid/organization/pie/radar/sankey/scatter/treemap/venn/violin/waterfall/word_cloud/district_map/path_map/pin_map/fishbone_diagram/flow_diagram/network_graph/mind_map
- preferred_currency: CNY/USD/EUR/JPY/HKD/GBP
- preferred_language: zh/en

Rules:
1. Only return a candidate when the user explicitly changes a long-term preference
   (e.g. "以后都用表格", "默认用美元"). A one-off request ("这次用表格") is NOT a candidate.
2. Do NOT extract suppliers, queries, approvals, permissions or security rules.
3. Return ONLY JSON: {"candidates":[{"memory_type":"...","value":"...","confidence":0.95}]}
4. Empty candidates if none.

User message:
{message}
"""


def message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def message_source_id(
    message: BaseMessage, thread_id: str, index: int
) -> str:
    message_id = getattr(message, "id", None)
    if message_id:
        return str(message_id)
    if getattr(message, "type", None) == "tool":
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            return str(tool_call_id)
    return f"{thread_id or 'thread'}:{index}"


class MemoryCandidateGenerator:
    def __init__(
        self,
        model: BaseChatModel | None = None,
        *,
        inferred_min_confidence: float = 0.6,
        max_suppliers: int = 5,
    ):
        self._model = model
        self._min_confidence = inferred_min_confidence
        self._max_suppliers = max_suppliers

    async def generate(
        self,
        messages: Sequence[BaseMessage],
        *,
        thread_id: str = "",
        request_id: str = "",
    ) -> list[MemoryCandidate]:
        if not messages:
            return []

        last_human_index = self._last_human_index(messages)
        if last_human_index is None:
            return []
        user_message = messages[last_human_index]
        user_text = message_text(user_message).strip()
        if not user_text:
            return []

        if _SOCIAL_ONLY.fullmatch(user_text):
            return []

        has_erp = self._has_erp_intent(user_text)
        has_explicit_marker = any(marker in user_text for marker in _EXPLICIT_MARKERS)
        if not has_erp and not has_explicit_marker:
            return []

        source_id = message_source_id(user_message, thread_id, last_human_index)
        now = datetime.now(timezone.utc)
        candidates: list[MemoryCandidate] = []

        # 显式偏好：LLM 只做语义判断，结果再经模型白名单校验。
        if has_explicit_marker and self._model is not None:
            candidates.extend(
                await self._explicit_candidates(
                    user_text, source_id=source_id, created_at=now
                )
            )

        if has_erp:
            query = user_text[:200]
            candidates.append(
                MemoryCandidate(
                    memory_type="recent_queries",
                    value=query,
                    origin="inferred",
                    source_message_id=source_id,
                    created_at=now,
                    confidence=0.7,
                    source="user_message",
                )
            )
            candidates.extend(
                self._tool_supplier_candidates(
                    messages,
                    start_index=last_human_index + 1,
                    thread_id=thread_id,
                    user_text=user_text,
                    created_at=now,
                )
            )

        return self._dedupe(candidates)

    @staticmethod
    def _last_human_index(messages: Sequence[BaseMessage]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if getattr(messages[index], "type", None) == "human":
                return index
        return None

    @staticmethod
    def _has_erp_intent(text: str) -> bool:
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in _ERP_KEYWORDS)

    async def _explicit_candidates(
        self,
        user_text: str,
        *,
        source_id: str,
        created_at: datetime,
    ) -> list[MemoryCandidate]:
        try:
            response = await self._model.ainvoke(_LLM_PROMPT.replace("{message}", user_text))
            text = message_text(response).strip()
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end <= start:
                return []
            payload = json.loads(text[start : end + 1])
            raw_candidates = payload.get("candidates", [])
            if not isinstance(raw_candidates, list):
                return []
        except Exception:
            logger.warning("MemoryCandidateGenerator: explicit LLM extraction failed")
            return []

        result: list[MemoryCandidate] = []
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                continue
            memory_type = raw.get("memory_type")
            value = raw.get("value")
            if memory_type not in EXPLICIT_MEMORY_TYPES or not isinstance(value, str):
                continue
            try:
                UserPreferences.model_validate(
                    {
                        "schema_version": CURRENT_SCHEMA_VERSION,
                        "revision": 0,
                        memory_type: value,
                    }
                )
            except ValidationError:
                continue
            try:
                confidence = float(raw.get("confidence", 0.9))
            except (TypeError, ValueError):
                confidence = 0.9
            confidence = max(0.8, min(1.0, confidence))
            result.append(
                MemoryCandidate(
                    memory_type=memory_type,
                    value=value,
                    origin="explicit",
                    source_message_id=source_id,
                    created_at=created_at,
                    confidence=confidence,
                    source="user_message",
                )
            )
        return result

    def _tool_supplier_candidates(
        self,
        messages: Sequence[BaseMessage],
        *,
        start_index: int,
        thread_id: str,
        user_text: str,
        created_at: datetime,
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        seen: set[str] = set()

        for index in range(start_index, len(messages)):
            message = messages[index]
            if getattr(message, "type", None) != "tool":
                continue
            tool_name = str(getattr(message, "name", "") or "")
            confidence = _TOOL_CONFIDENCE.get(tool_name)
            if confidence is None or confidence < self._min_confidence:
                continue

            payload = self._parse_tool_payload(getattr(message, "content", ""))
            if payload is None:
                continue
            names = self._extract_supplier_names(payload, tool_name)
            source_id = message_source_id(message, thread_id, index)

            for name in names:
                normalized = name.strip()
                if not normalized or normalized in seen:
                    continue
                if self._is_negated(user_text, normalized):
                    continue
                seen.add(normalized)
                candidates.append(
                    MemoryCandidate(
                        memory_type="recent_suppliers",
                        value=normalized,
                        origin="inferred",
                        source_message_id=source_id,
                        created_at=created_at,
                        confidence=confidence,
                        source="tool_result",
                    )
                )
                if len(candidates) >= self._max_suppliers:
                    return candidates
        return candidates

    @staticmethod
    def _parse_tool_payload(content: Any) -> Any:
        data = content
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None
        if isinstance(data, dict) and "ok" in data:
            if data.get("ok") is not True:
                return None
            data = data.get("data")
        return data

    def _extract_supplier_names(self, data: Any, tool_name: str) -> list[str]:
        names: list[str] = []

        def visit(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, dict):
                return
            for key in ("supplier_name", "supplierName"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    names.append(value.strip())
            if tool_name == "supplier_get":
                for key in ("name",):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value)

        visit(data)
        return names

    @staticmethod
    def _is_negated(user_text: str, supplier_name: str) -> bool:
        text = re.sub(r"\s+", "", user_text)
        name = re.sub(r"\s+", "", supplier_name)
        if not name:
            return False
        for prefix in _NEGATION_PREFIXES:
            if re.search(re.escape(prefix) + r".{0,12}" + re.escape(name), text):
                return True
        return False

    @staticmethod
    def _dedupe(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        seen: set[tuple[str, str, str]] = set()
        result: list[MemoryCandidate] = []
        for candidate in candidates:
            key = (candidate.memory_type, candidate.value, candidate.origin)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result
