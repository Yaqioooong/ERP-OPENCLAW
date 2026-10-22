"""采购需求解析器。

不依赖 provider 的 response_format/json_schema：
- 只用普通 chat 调用，要求模型输出 JSON；
- 用 Pydantic 做严格校验；
- 解析失败时带上错误信息重试一次。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.procurement.models import ProcurementRequest

_SYSTEM_PROMPT = """你是 ERP 采购需求解析器。请从用户输入中提取结构化采购条件。

规则:
1. 只提取用户明确表达的信息，不要猜测缺失字段。
2. 数量必须是用户给出的数字，不要自行换算单位。
3. 仓库只提取用户明确说明的仓库/库位。
4. 预算只在用户明确给出时填写。
5. 物料名称、型号、编码保留原文，不要改写。
6. 如果本轮是补充上一轮信息，请把补充内容合并到 previous_request，保留未修改字段。
7. 无法确定的字段留空，由后续校验流程向用户追问。
8. 只输出一个 JSON 对象，不要输出 Markdown 代码块、解释文字或前后缀。

JSON Schema:
{schema}
"""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "text" in item:
                    parts.append(str(item["text"]))
        return "".join(parts)
    return str(content or "")


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("模型返回为空")

    # 容错：模型偶尔会加 ```json ... ```
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型返回中没有 JSON 对象")

    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    return payload


class LLMRequestParser:
    def __init__(self, model):
        # 解析需要稳定输出，建议使用 temperature 较低的 SUMMARY_MODEL，而不是主 Agent 的 1.1
        self._model = model
        self._system_prompt = _SYSTEM_PROMPT.format(
            schema=json.dumps(
                ProcurementRequest.model_json_schema(),
                ensure_ascii=False,
            )
        )

    async def parse(
        self,
        raw_text: str,
        *,
        previous: dict[str, Any] | None = None,
        answer: Any = None,
    ) -> ProcurementRequest:
        context = {
            "raw_text": raw_text,
            "previous_request": previous or {},
            "answer": answer,
        }
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(
                content=json.dumps(context, ensure_ascii=False, default=str)
            ),
        ]

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                message = await self._model.ainvoke(messages)
                text = _content_to_text(getattr(message, "content", message))
                return ProcurementRequest.model_validate(_extract_json(text))
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    messages = messages + [
                        HumanMessage(
                            content=(
                                "上一次输出不是合法的 ProcurementRequest JSON："
                                f"{exc}。请只输出一个 JSON 对象，不要加 Markdown 或解释。"
                            )
                        )
                    ]

        raise ValueError(f"REQUEST_PARSE_FAILED: {last_error}") from last_error

    async def merge(
        self,
        previous: ProcurementRequest,
        answer: Any,
    ) -> ProcurementRequest:
        # 如果前端直接给 request_patch，优先做确定性合并，避免再调一次模型
        if isinstance(answer, dict) and isinstance(answer.get("request_patch"), dict):
            data = previous.model_dump(mode="json")
            _deep_merge(data, answer["request_patch"])
            return ProcurementRequest.model_validate(data)

        # 自由文本补充，用模型合并
        return await self.parse(
            raw_text="",
            previous=previous.model_dump(mode="json"),
            answer=answer,
        )


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
