"""采购请求的确定性归一化与启动前结构校验。

模型、Agent 工具和 HTTP API 都可能给出别名或宽松类型。所有入口先经过
本模块，避免把“供应商名称”“数字字符串”等输入留给后台状态机后才失败。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from agent.procurement.models import ProcurementRequest


class ProcurementRequestInputError(ValueError):
    """采购请求结构不合法；错误可安全返回给调用方并映射到 422。"""

    def __init__(self, errors: list[dict[str, str]]):
        self.code = "INVALID_PROCUREMENT_REQUEST"
        self.errors = errors
        super().__init__("采购请求结构不合法")

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "errors": self.errors}


def _field_path(location: tuple[Any, ...]) -> str:
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        elif path:
            path += f".{part}"
        else:
            path = str(part)
    return path or "request"


def _normalize_supplier_fields(data: dict[str, Any]) -> None:
    # 兼容少量常见外部别名，统一为领域模型字段。
    if data.get("preferred_supplier_name") is None:
        data["preferred_supplier_name"] = data.pop("supplier_name", None)
    else:
        data.pop("supplier_name", None)

    supplier_name = data.get("preferred_supplier_name")
    if isinstance(supplier_name, str):
        supplier_name = supplier_name.strip()
        data["preferred_supplier_name"] = supplier_name or None

    if "preferred_supplier_id" not in data and "supplier_id" in data:
        data["preferred_supplier_id"] = data.pop("supplier_id")
    else:
        data.pop("supplier_id", None)

    supplier_id = data.get("preferred_supplier_id")
    if not isinstance(supplier_id, str):
        return

    value = supplier_id.strip()
    if not value:
        data["preferred_supplier_id"] = None
        return
    if value.isdigit():
        data["preferred_supplier_id"] = int(value)
        return

    # 名称被误放进 ID 字段时转存到可追溯的名称字段，绝不猜测供应商编号。
    data.pop("preferred_supplier_id", None)
    if not data.get("preferred_supplier_name"):
        data["preferred_supplier_name"] = value


def normalize_procurement_request(request: dict[str, Any] | None) -> dict[str, Any]:
    """返回应用了字段别名规则的请求副本，不执行 Pydantic 类型校验。"""
    data = deepcopy(request or {})
    _normalize_supplier_fields(data)

    normalized_items: list[Any] = []
    raw_items = data.get("items")
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            normalized_items.append(raw)
            continue
        item = dict(raw)
        if item.get("part_id") is None:
            item["part_id"] = (
                item.get("material_id")
                or item.get("partId")
            )
        if item.get("part_name") is None:
            item["part_name"] = (
                item.get("material_name")
                or item.get("partName")
                or item.get("name")
            )
        if item.get("part_code") is None:
            item["part_code"] = (
                item.get("material_code")
                or item.get("partCode")
            )
        if item.get("quantity") is None:
            item["quantity"] = item.get("qty")
        for alias in (
            "material_id",
            "material_name",
            "material_code",
            "part",
            "partId",
            "partName",
            "partCode",
            "name",
            "qty",
        ):
            item.pop(alias, None)
        normalized_items.append(item)
    if isinstance(raw_items, list):
        data["items"] = normalized_items
    return data


def validate_procurement_request(
    request: dict[str, Any] | None,
) -> ProcurementRequest:
    """归一化并校验请求；把 Pydantic 错误转成稳定、可展示的字段路径。"""
    normalized = normalize_procurement_request(request)
    try:
        return ProcurementRequest.model_validate(normalized)
    except ValidationError as exc:
        errors = [
            {
                "field": _field_path(tuple(error.get("loc") or ())),
                "message": str(error.get("msg") or "字段值不合法"),
            }
            for error in exc.errors()
        ]
        raise ProcurementRequestInputError(errors) from exc


def merge_procurement_request(
    previous: ProcurementRequest,
    patch: dict[str, Any],
) -> ProcurementRequest:
    """把补充 patch 深合并到原请求，再走同一归一化与校验。"""
    data = previous.model_dump(mode="json")
    _deep_merge(data, patch)
    return validate_procurement_request(data)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
