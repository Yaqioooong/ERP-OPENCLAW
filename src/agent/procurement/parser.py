import json
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from agent.procurement.models import ProcurementRequest

_SYSTEM_PROMPT = """你是 ERP 采购需求解析器。请从用户输入中提取结构化采购条件

规则:
1. 只提取用户明确表达的信息，不要猜测缺失字段。
2. 数量必须是用户给出的数字，不要自行换算单位。
3. 仓库只提取用户明确说明的仓库/库位。
4. 预算只在用户明确给出时填写。
5. 物料名称、型号、编码保留原文，不要改写。
6. 如果本轮是补充上一轮信息，请把补充内容合并到previous_request, 保留未修改字段。
7. 无法确定的字段留空，有后续校验流程向用户追问。
"""


class LLMRequestParser:
    def __init__(self, model):
        # 解析需要稳定输出，建议使用temperature 较低的SUMMARY_MODEL, 而不是主Agent 的1.1
        self._model = model
        self._structured = model.with_structured_output(ProcurementRequest)

    async def parse(
            self,
            raw_text: str,
            *,
            previous: dict[str, Any] | None = None,
            answer: Any = None
    ) -> ProcurementRequest:
        context = {
            "raw_text": raw_text,
            "previous_request": previous or {},
            "answer": answer
        }
        result = await self._structured.ainvoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                # 【Python小课堂】json.dumps = json dump string把 Python 对象（dict、list 等）序列化成 JSON 格式的字符串。
                # json.dumps → 对象转字符串（dump string）
                # json.dump → 对象写进文件：json.dump(obj, f)
                # json.loads → 字符串转对象
                # json.load → 从文件读成对象
                HumanMessage(content=json.dumps(context, ensure_ascii=False, default=str))
            ]
        )
        if isinstance(result, ProcurementRequest):
            return result
        return ProcurementRequest.model_validate(result)

    async def merge(
            self,
            previous: ProcurementRequest,
            answer: Any
    ) -> ProcurementRequest:
        # 如果前端直接给request_patch, 优先做确定性合并，避免再调一次模型
        if isinstance(answer, dict) and isinstance(answer.get("request_patch"), dict):
            data = previous.model_dump(mode="json")
            _deep_merge(data, answer["request_patch"])
            return ProcurementRequest.model_validate(data)

        # 自由文本补充，用模型合并
        return await self.parse(
            raw_text="",
            previous=previous.model_dump(mode="json"),
            answer=answer
        )


def _deep_merge(base: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
