"""把 MCP 工具返回的原始 JSON 归一化成 CandidateSet。

字段名以实际 ERP 返回为准，所有 camelCase/snake_case 差异集中在这里，
不污染状态机和规则层。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

from agent.procurement.config import as_business_datetime
from agent.procurement.models import (
    CandidateSet,
    InventorySnapshot,
    ProcurementRequest,
    SupplierOffer,
)
from agent.procurement.ports import (
    PartResolution,
    PartResolutionError,
    SupplierResolution,
    SupplierResolutionError,
)


_PART_SEARCH_TOKEN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/+~-]*|[\u3400-\u9fff]+"
)


class UpstreamUnavailable(RuntimeError):
    """MCP 工具不可用/返回异常；由 analyze 节点转成统一错误码。"""

    def __init__(self, message: str, *, code: str = "UPSTREAM_UNAVAILABLE"):
        super().__init__(message)
        self.code = code


def _pick(record: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(record, dict):
        return default
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("records", "data", "items", "list"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
        return [value]
    return []


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    # 带时区值先转换到业务时区；无时区存量值按 ERP 业务本地时间解释。
    return as_business_datetime(parsed)


def _unwrap_mcp_content(result: Any) -> Any:
    """LangChain MCP adapter 可能返回 [{'type': 'text', 'text': ...}]。"""
    if isinstance(result, list):
        texts = [
            item.get("text")
            for item in result
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if texts:
            return "\n".join(texts)
    return result


def _normalize_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")

    result = _unwrap_mcp_content(result)

    if isinstance(result, str):
        text = result.strip()
        if not text:
            return []
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            if "报错" in text or "API error" in text:
                raise UpstreamUnavailable(text)
            return text

    if isinstance(result, dict) and "ok" in result:
        if result.get("ok") is True:
            return result.get("data")
        error = result.get("error") or {}
        code = str(error.get("code") or "UPSTREAM_UNAVAILABLE")
        message = str(error.get("message") or error)
        raise UpstreamUnavailable(message, code=code)

    if isinstance(result, list):
        for item in result:
            if isinstance(item, str) and ("报错" in item or "API error" in item):
                raise UpstreamUnavailable(item)
        return result

    if isinstance(result, dict) and result.get("error"):
        error = result["error"]
        if isinstance(error, dict):
            raise UpstreamUnavailable(
                str(error.get("message") or error),
                code=str(error.get("code") or "UPSTREAM_UNAVAILABLE"),
            )
        raise UpstreamUnavailable(str(error))

    return result


def _normalize_part(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _to_int(_pick(record, "id", "partId", "part_id")),
        "name": str(_pick(record, "name", "partName", "part_name") or "").strip(),
        "code": _pick(record, "partCode", "part_code", "code"),
        "purchase_price": _to_decimal(
            _pick(record, "purchasePrice", "purchase_price")
        ),
        "supplier_id": _to_int(_pick(record, "supplierId", "supplier_id")),
        "supplier_name": _pick(record, "supplierName", "supplier_name"),
        "supplier_status": _to_int(
            _pick(record, "supplierStatus", "supplier_status")
        ),
        "credit_rating": _pick(record, "creditRating", "credit_rating"),
    }


def _part_search_terms(value: str) -> list[str]:
    """生成整句与关键词查询；保持顺序稳定并优先产品型号。"""
    text = " ".join(str(value or "").split())
    if not text:
        return []

    terms = [text[:100]]
    tokens = [
        token
        for token in _PART_SEARCH_TOKEN.findall(text)
        if len(token) >= 2
    ]
    tokens.sort(
        key=lambda token: (
            not (
                any(char.isdigit() for char in token)
                and any(char.isalpha() for char in token)
            ),
            not token.isascii(),
            -len(token),
        )
    )

    seen = {terms[0].casefold()}
    for token in tokens:
        normalized = token[:100]
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(normalized)
        if len(terms) >= 6:
            break
    return terms


def _normalize_order_detail(
        detail: dict[str, Any], *, fallback_part: dict[str, Any]
) -> dict[str, Any]:
    part_detail = _pick(detail, "partDetail", "part_detail") or {}
    supplier = _pick(detail, "supplier") or {}

    part_id = (
            _to_int(_pick(part_detail, "id", "partId", "part_id"))
            or _to_int(_pick(detail, "partId", "part_id"))
            or fallback_part.get("id")
    )
    part_name = (
            _pick(part_detail, "name", "partName", "part_name")
            or _pick(detail, "partName", "part_name")
            or fallback_part.get("name")
    )

    order_time = _parse_datetime(_pick(detail, "orderTime", "order_time"))
    expected_delivery = _parse_datetime(
        _pick(detail, "expectedDeliveryDate", "expected_delivery_date")
    )
    actual_delivery = _parse_datetime(
        _pick(detail, "actualDeliveryDate", "actual_delivery_date")
    )
    lead_time_days = None
    lead_time_source = None
    if order_time:
        if actual_delivery:
            days = (actual_delivery.date() - order_time.date()).days
            if days >= 0:
                lead_time_days = days
                lead_time_source = "actual_delivery"
        if lead_time_days is None and expected_delivery:
            days = (expected_delivery.date() - order_time.date()).days
            if days >= 0:
                lead_time_days = days
                lead_time_source = "expected_delivery"

    return {
        "supplier_id": (
                _to_int(_pick(supplier, "id", "supplierId", "supplier_id"))
                or _to_int(_pick(detail, "supplierId", "supplier_id"))
        ),
        "supplier_name": (
                _pick(supplier, "name", "supplierName", "supplier_name")
                or _pick(detail, "supplierName", "supplier_name")
        ),
        "supplier_status": _to_int(
            _pick(supplier, "status", "supplierStatus", "supplier_status")
        ),
        "credit_rating": _pick(supplier, "creditRating", "credit_rating"),
        "part_id": part_id,
        "part_name": str(part_name or "").strip(),
        "unit_price": _to_decimal(_pick(detail, "unitPrice", "unit_price")),
        "order_time": order_time,
        "lead_time_days": lead_time_days,
        "lead_time_source": lead_time_source,
    }


class McpProcurementDataProvider:
    def __init__(self, tools: dict[str, Any], *, active_supplier_statuses: set[int]):
        self._tools = tools
        self._active = active_supplier_statuses

    async def collect(self, request: ProcurementRequest) -> CandidateSet:
        parts = []
        for item in request.items:
            try:
                parts.append(await self._resolve_part(item))
            except PartResolutionError as exc:
                # collect 是执行层入口；正常流程已在 validate_request 处理业务
                # 错误。保留 UpstreamUnavailable 兼容旧调用方和异常边界。
                raise UpstreamUnavailable(str(exc), code=exc.code) from exc
        inventory = await self._inventory(request, parts)
        offers = await self._offers(request, parts, inventory)
        warnings = list(
            dict.fromkeys(
                warning
                for offer in offers
                for warning in offer.data_warnings
            )
        )
        return CandidateSet(
            offers=offers,
            inventory=inventory,
            data_warnings=warnings,
        )

    async def resolve_part(self, item) -> PartResolution:
        """把名称、编码或 ID 解析成唯一物料，供请求校验阶段回写。"""
        part = await self._resolve_part(item)
        code = str(part.get("code") or "").strip() or None
        return PartResolution(
            id=part["id"],
            name=part["name"],
            code=code,
        )

    async def resolve(
        self,
        *,
        supplier_id: int | None,
        supplier_name: str | None,
    ) -> SupplierResolution:
        """按 ID 或名称把供应商解析成唯一业务主体。

        名称只接受目录中的精确匹配；模糊查询命中多项时保留候选并要求用户
        选择，不把品牌名或物料默认供应商当作业务主体。
        """
        if supplier_id is not None:
            try:
                data = await self._call_tool(
                    "supplier_get", {"supplier_id": supplier_id}
                )
            except UpstreamUnavailable as exc:
                if exc.code in {"MCP_TOOL_MISSING", "UPSTREAM_UNAVAILABLE"}:
                    raise
                raise SupplierResolutionError(
                    code="SUPPLIER_NOT_FOUND",
                    message=f"供应商 {supplier_id} 无法读取。",
                ) from exc
            if not isinstance(data, dict) or not data:
                raise SupplierResolutionError(
                    code="SUPPLIER_NOT_FOUND",
                    message=f"供应商 {supplier_id} 不存在。",
                )
            return self._supplier_resolution(data, fallback_id=supplier_id)

        name = str(supplier_name or "").strip()
        if not name:
            raise SupplierResolutionError(
                code="SUPPLIER_NOT_PROVIDED",
                message="未提供可解析的供应商。",
            )

        records = await self._call_tool(
            "supplier_query", {"params": {"name": name}}
        )
        candidates = [
            normalized
            for record in _as_list(records)
            if isinstance(record, dict)
            for normalized in [self._normalize_supplier_record(record)]
            if normalized["id"] is not None
        ]
        exact = [
            candidate
            for candidate in candidates
            if str(candidate["name"]).strip().casefold() == name.casefold()
        ]

        if not exact and len(candidates) == 1:
            exact = candidates

        if not exact:
            if len(candidates) > 1:
                choices = "；".join(
                    f"{item['id']}-{item['name']}" for item in candidates
                )
                raise SupplierResolutionError(
                    code="SUPPLIER_AMBIGUOUS",
                    message=(
                        f"供应商“{name}”匹配到多个主体，请填写供应商 ID："
                        f"{choices}。"
                    ),
                    candidates=[
                        {"id": item["id"], "name": item["name"]}
                        for item in candidates
                    ],
                )
            raise SupplierResolutionError(
                code="SUPPLIER_NOT_FOUND",
                message=f"未找到名称精确匹配“{name}”的供应商。",
                candidates=candidates,
            )
        if len(exact) > 1:
            choices = "；".join(
                f"{item['id']}-{item['name']}" for item in exact
            )
            raise SupplierResolutionError(
                code="SUPPLIER_AMBIGUOUS",
                message=f"供应商“{name}”匹配到多个主体，请填写供应商 ID：{choices}。",
                candidates=[
                    {"id": item["id"], "name": item["name"]}
                    for item in exact
                ],
            )

        item = exact[0]
        return self._supplier_resolution(item, fallback_id=item["id"])

    @staticmethod
    def _normalize_supplier_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": _to_int(_pick(record, "id", "supplierId", "supplier_id")),
            "name": str(
                _pick(record, "name", "supplierName", "supplier_name") or ""
            ).strip(),
            "status": _to_int(_pick(record, "status", "supplierStatus")),
        }

    def _supplier_resolution(
        self,
        record: dict[str, Any],
        *,
        fallback_id: int,
    ) -> SupplierResolution:
        item = self._normalize_supplier_record(record)
        supplier_id = item["id"] or fallback_id
        status = item["status"]
        if status is not None and status not in self._active:
            raise SupplierResolutionError(
                code="SUPPLIER_UNAVAILABLE",
                message=f"供应商 {supplier_id} 当前状态不可用。",
            )
        return SupplierResolution(
            id=supplier_id,
            name=item["name"] or f"SUP-{supplier_id}",
            status=status,
        )

    async def _search_parts(self, name: str) -> list[dict[str, Any]]:
        """用整句查询并仅在需要时拆词，按命中次数收敛候选。"""
        terms = _part_search_terms(name)
        if not terms:
            return []

        candidates: dict[int, dict[str, Any]] = {}
        hit_counts: dict[int, int] = {}
        for index, term in enumerate(terms):
            records = await self._call_tool("part_search", {"name": term})
            found = [
                part
                for part in (_normalize_part(record) for record in _as_list(records))
                if part["id"] is not None
            ]
            if index == 0 and len(found) == 1:
                return found
            for part in found:
                part_id = part["id"]
                candidates[part_id] = part
                hit_counts[part_id] = hit_counts.get(part_id, 0) + 1

        if not candidates:
            return []
        best_hits = max(hit_counts.values())
        return [
            part
            for part_id, part in candidates.items()
            if hit_counts[part_id] == best_hits
        ]

    async def _resolve_part(self, item) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []

        if item.part_id is not None:
            # 用户给出的稳定 ID 是权威标识；名称只用于描述和追溯，不能因为
            # 模糊搜索的词序差异阻断已确认物料。
            record = await self._call_tool(
                "part_get", {"part_id": item.part_id}
            )
            candidates = [_normalize_part(record)] if record else []
            candidates = [p for p in candidates if p["id"] == item.part_id]
            if candidates and item.part_code:
                requested_code = str(item.part_code).strip().lower()
                formal_code = str(candidates[0].get("code") or "").strip().lower()
                if formal_code != requested_code:
                    raise PartResolutionError(
                        code="PART_CODE_MISMATCH",
                        message=(
                            f"物料 ID {item.part_id} 的正式编码为 "
                            f"{candidates[0].get('code') or '未知'}，"
                            f"与用户提供的编码 {item.part_code} 不一致，"
                            "请确认使用哪个标识。"
                        ),
                    )
            if candidates:
                item.part_name = candidates[0]["name"]
        elif item.part_name:
            candidates = await self._search_parts(item.part_name)
            if item.part_code:
                code = str(item.part_code).strip().lower()
                candidates = [
                    p for p in candidates
                    if str(p.get("code") or "").strip().lower() == code
                ]
            if len(candidates) > 1:
                exact = [
                    p for p in candidates
                    if p["name"].strip() == str(item.part_name).strip()
                ]
                if len(exact) == 1:
                    candidates = exact
        elif item.part_code:
            records = await self._call_tool(
                "part_query",
                {"params": {"current": 1, "size": 50, "name": item.part_code}},
            )
            candidates = [
                p for p in (_normalize_part(r) for r in _as_list(records))
                if p["id"] is not None
            ]
            code = str(item.part_code).strip().lower()
            candidates = [
                p for p in candidates
                if str(p.get("code") or "").strip().lower() == code
            ]
        else:
            raise PartResolutionError(
                code="PART_IDENTIFIER_MISSING",
                message="缺少物料名称、编码或 ID。",
            )

        if not candidates:
            raise PartResolutionError(
                code="PART_NOT_FOUND",
                message=(
                    "没有找到物料 "
                    f"{item.part_name or item.part_code or item.part_id}；"
                    "请确认名称或编码，或填写物料 ID。"
                ),
            )
        if len(candidates) > 1:
            choices = "；".join(
                f"{part['id']}-{part['name']}"
                + (f"（{part['code']}）" if part.get("code") else "")
                for part in candidates
            )
            raise PartResolutionError(
                code="PART_AMBIGUOUS",
                message=(
                    f"物料“{item.part_name or item.part_code}”匹配到多个记录，"
                    f"请填写物料 ID：{choices}。"
                ),
                candidates=[
                    {
                        "id": part["id"],
                        "name": part["name"],
                        "code": part.get("code"),
                    }
                    for part in candidates
                ],
            )
        return candidates[0]

    async def _inventory(
            self, request: ProcurementRequest, parts: list[dict[str, Any]]
    ) -> list[InventorySnapshot]:
        snapshots: list[InventorySnapshot] = []

        for part in parts:
            params: dict[str, Any] = {
                "current": 1,
                "size": 50,
                "partName": part["name"],
            }
            if request.warehouse_location:
                params["warehouseLocation"] = request.warehouse_location

            try:
                records = await self._call_tool("inventory_page", {"params": params})
            except UpstreamUnavailable:
                records = []

            matched: list[InventorySnapshot] = []
            for record in _as_list(records):
                part_id = _to_int(_pick(record, "partId", "part_id"))
                if part_id is not None and part_id != part["id"]:
                    continue
                matched.append(
                    InventorySnapshot(
                        part_id=part["id"],
                        part_name=part["name"],
                        warehouse_location=_pick(
                            record, "warehouseLocation", "warehouse_location"
                        ),
                        current_quantity=_to_int(
                            _pick(record, "currentQuantity", "current_quantity")
                        ),
                        safety_stock=_to_int(
                            _pick(record, "safetyStock", "safety_stock")
                        ),
                    )
                )

            if not matched:
                # 查不到库存时保留一条“未知”快照，不要把它当成 0
                matched.append(
                    InventorySnapshot(
                        part_id=part["id"],
                        part_name=part["name"],
                        warehouse_location=request.warehouse_location,
                        current_quantity=None,
                        safety_stock=None,
                    )
                )
            snapshots.append(matched[0])

        return snapshots

    async def _offers(
            self,
            request: ProcurementRequest,
            parts: list[dict[str, Any]],
            inventory: list[InventorySnapshot],
    ) -> list[SupplierOffer]:
        offers: list[SupplierOffer] = []

        for part in parts:
            try:
                histories = _as_list(
                    await self._call_tool(
                        "order_search_details", {"params": {"partName": part["name"]}}
                    )
                )
            except UpstreamUnavailable:
                histories = []

            suppliers: dict[int, dict[str, Any]] = {}
            supplier_history: dict[int, list[dict[str, Any]]] = {}

            if part.get("supplier_id") is not None:
                suppliers[part["supplier_id"]] = {
                    "id": part["supplier_id"],
                    "name": part.get("supplier_name") or f"SUP-{part['supplier_id']}",
                    "status": part.get("supplier_status"),
                    "credit": part.get("credit_rating"),
                }

            for detail in histories:
                if not isinstance(detail, dict):
                    continue
                normalized = _normalize_order_detail(detail, fallback_part=part)
                # 同名物料可能有多条历史，必须按 part_id 过滤，避免拿错价格
                if (
                        normalized["part_id"] is not None
                        and normalized["part_id"] != part["id"]
                ):
                    continue
                supplier_id = normalized["supplier_id"]
                if supplier_id is None:
                    continue
                supplier = suppliers.setdefault(
                    supplier_id,
                    {
                        "id": supplier_id,
                        "name": normalized["supplier_name"] or f"SUP-{supplier_id}",
                        "status": normalized["supplier_status"],
                        "credit": normalized["credit_rating"],
                    },
                )
                if normalized["supplier_name"]:
                    supplier["name"] = normalized["supplier_name"]
                if normalized["supplier_status"] is not None:
                    supplier["status"] = normalized["supplier_status"]
                if normalized["credit_rating"]:
                    supplier["credit"] = normalized["credit_rating"]
                supplier_history.setdefault(supplier_id, []).append(normalized)

            if request.preferred_supplier_id is not None:
                suppliers.setdefault(
                    request.preferred_supplier_id,
                    {
                        "id": request.preferred_supplier_id,
                        "name": f"SUP-{request.preferred_supplier_id}",
                        "status": None,
                        "credit": None,
                    },
                )

            for supplier_id, supplier in suppliers.items():
                detail = await self._supplier_detail(supplier_id)
                if not detail:
                    continue
                if detail.get("name"):
                    supplier["name"] = detail["name"]
                if detail.get("status") is not None:
                    supplier["status"] = detail["status"]
                if detail.get("credit"):
                    supplier["credit"] = detail["credit"]

            snapshot = next(
                (s for s in inventory if s.part_id == part["id"]), None
            )
            available_quantity = snapshot.current_quantity if snapshot else None

            for supplier_id, supplier in suppliers.items():
                rows = supplier_history.get(supplier_id, [])
                latest = max(
                    rows,
                    key=lambda row: (
                        row["order_time"].timestamp()
                        if row.get("order_time")
                        else float("-inf")
                    ),
                    default=None,
                )
                unit_price = latest["unit_price"] if latest else None
                price_source = "historical_order" if unit_price is not None else None

                if unit_price is None and part.get("purchase_price") is not None:
                    unit_price = part["purchase_price"]
                    price_source = "part_master"

                lead_times = [
                    row["lead_time_days"]
                    for row in rows
                    if row.get("lead_time_days") is not None
                ]
                lead_time_days = (
                    int(round(median(lead_times))) if lead_times else None
                )
                lead_time_sources = {
                    row["lead_time_source"]
                    for row in rows
                    if row.get("lead_time_source") is not None
                }
                if "expected_delivery" in lead_time_sources:
                    lead_time_source = "expected_delivery"
                elif lead_time_sources == {"actual_delivery"}:
                    lead_time_source = "actual_delivery"
                else:
                    lead_time_source = None
                data_warnings = []
                if lead_time_source == "expected_delivery":
                    data_warnings.append(
                        "历史实际交期缺失，交期按历史预计到货日期估算。"
                    )

                offers.append(
                    SupplierOffer(
                        supplier_id=supplier_id,
                        supplier_name=supplier.get("name") or f"SUP-{supplier_id}",
                        supplier_status=supplier.get("status"),
                        part_id=part["id"],
                        part_name=part["name"],
                        unit_price=unit_price,
                        price_source=price_source,
                        lead_time_days=lead_time_days,
                        lead_time_source=lead_time_source,
                        available_quantity=available_quantity,
                        credit_rating=supplier.get("credit"),
                        data_warnings=data_warnings,
                    )
                )

        return offers

    async def _call_tool(self, name: str, payload: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise UpstreamUnavailable(
                f"MCP tool missing: {name}", code="MCP_TOOL_MISSING"
            )
        try:
            result = await tool.ainvoke(payload)
        except Exception as first:
            # 少数 MCP 包装器要求单个 params 对象；只对只读工具做一次兼容重试。
            if "params" not in payload:
                try:
                    result = await tool.ainvoke({"params": payload})
                except Exception:
                    raise UpstreamUnavailable(
                        f"{name} failed: {first}", code="UPSTREAM_UNAVAILABLE"
                    ) from first
            else:
                raise UpstreamUnavailable(
                    f"{name} failed: {first}", code="UPSTREAM_UNAVAILABLE"
                ) from first
        return _normalize_result(result)

    async def _supplier_detail(self, supplier_id: int) -> dict[str, Any]:
        try:
            data = await self._call_tool(
                "supplier_get", {"supplier_id": supplier_id}
            )
        except UpstreamUnavailable:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            "id": supplier_id,
            "name": data.get("name") or f"SUP-{supplier_id}",
            "status": data.get("status"),
            "credit": data.get("credit_rating"),
        }
