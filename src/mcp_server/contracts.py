from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    BUSINESS_REJECTED = "BUSINESS_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolError(ContractModel):
    code: ToolErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolMeta(ContractModel):
    tool: str
    request_id: str
    elapsed_ms: int = 0
    upstream_code: int | None = None


class ToolResponse(ContractModel):
    ok: bool
    data: Any | None = None
    error: ToolError | None = None
    meta: ToolMeta


# ============================================================
# 输出模型
# ============================================================
class PartRecord(ContractModel):
    id: int | None = None
    name: str = ""
    code: str | None = None
    purchase_price: Decimal | None = None
    supplier_id: int | None = None
    supplier_name: str | None = None
    supplier_status: int | None = None
    credit_rating: str | None = None


class SupplierRecord(ContractModel):
    id: int | None = None
    name: str = ""
    status: int | None = None
    credit_rating: str | None = None
    contact: str | None = None
    phone: str | None = None
    address: str | None = None


class InventoryRecord(ContractModel):
    part_id: int | None = None
    part_name: str = ""
    warehouse_location: str | None = None
    current_quantity: int | None = None
    safety_stock: int | None = None


class OrderDetailRecord(ContractModel):
    order_id: int | None = None
    order_number: str | None = None
    part_id: int | None = None
    part_name: str | None = None
    quantity: int | None = None
    unit_price: Decimal | None = None
    subtotal: Decimal | None = None
    supplier_id: int | None = None
    supplier_name: str | None = None
    supplier_status: int | None = None
    credit_rating: str | None = None
    order_time: datetime | None = None
    actual_delivery_date: date | None = None


class OrderRecord(ContractModel):
    order_id: int | None = None
    order_number: str | None = None
    status: int | None = None
    total_amount: Decimal | None = None
    order_time: datetime | None = None
    expected_delivery_date: date | None = None
    actual_delivery_date: date | None = None
    supplier_id: int | None = None
    supplier_name: str | None = None
    details: list[OrderDetailRecord] = Field(default_factory=list)


# ============================================================
# 字段解析
# ============================================================
def pick(record: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(record, dict):
        return default
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return None


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return None


def as_date(value: Any) -> date | None:
    parsed = as_datetime(value)
    return parsed.date() if parsed else None


# ============================================================
# ERP -> 契约模型
# ============================================================
def normalize_part(raw: Any) -> PartRecord:
    return PartRecord(
        id=as_int(pick(raw, "id", "partId", "part_id")),
        name=str(pick(raw, "name", "partName", "part_name") or "").strip(),
        code=pick(raw, "code", "partCode", "part_code"),
        purchase_price=as_decimal(
            pick(raw, "purchasePrice", "purchase_price", "unitPrice", "unit_price")
        ),
        supplier_id=as_int(pick(raw, "supplierId", "supplier_id")),
        supplier_name=pick(raw, "supplierName", "supplier_name"),
        supplier_status=as_int(
            pick(raw, "supplierStatus", "supplier_status", "status")
        ),
        credit_rating=pick(raw, "creditRating", "credit_rating"),
    )


def normalize_supplier(raw: Any) -> SupplierRecord:
    return SupplierRecord(
        id=as_int(pick(raw, "id", "supplierId", "supplier_id")),
        name=str(pick(raw, "name", "supplierName", "supplier_name") or "").strip(),
        status=as_int(pick(raw, "status", "supplierStatus", "supplier_status")),
        credit_rating=pick(raw, "creditRating", "credit_rating"),
        contact=pick(raw, "contact", "contactName", "contact_name"),
        phone=pick(raw, "phone", "mobile", "telephone"),
        address=pick(raw, "address", "supplierAddress", "supplier_address"),
    )


def normalize_inventory(raw: Any) -> InventoryRecord:
    part = pick(raw, "partDetail", "part_detail") or {}
    return InventoryRecord(
        part_id=as_int(
            pick(raw, "partId", "part_id") or pick(part, "id", "partId", "part_id")
        ),
        part_name=str(
            pick(raw, "partName", "part_name")
            or pick(part, "name", "partName", "part_name")
            or ""
        ).strip(),
        warehouse_location=pick(
            raw, "warehouseLocation", "warehouse_location"
        ),
        current_quantity=as_int(
            pick(raw, "currentQuantity", "current_quantity")
        ),
        safety_stock=as_int(pick(raw, "safetyStock", "safety_stock")),
    )


def normalize_order_detail(raw: Any) -> OrderDetailRecord:
    part = pick(raw, "partDetail", "part_detail") or {}
    supplier = pick(raw, "supplier") or {}
    return OrderDetailRecord(
        order_id=as_int(pick(raw, "orderId", "order_id")),
        order_number=pick(raw, "orderNumber", "order_number"),
        part_id=as_int(
            pick(raw, "partId", "part_id") or pick(part, "id", "partId", "part_id")
        ),
        part_name=pick(
            raw, "partName", "part_name"
        ) or pick(part, "name", "partName", "part_name"),
        quantity=as_int(pick(raw, "quantity", "qty")),
        unit_price=as_decimal(pick(raw, "unitPrice", "unit_price")),
        subtotal=as_decimal(pick(raw, "subtotal", "subTotal")),
        supplier_id=as_int(
            pick(supplier, "id", "supplierId", "supplier_id")
            or pick(raw, "supplierId", "supplier_id")
        ),
        supplier_name=pick(supplier, "name", "supplierName", "supplier_name")
                      or pick(raw, "supplierName", "supplier_name"),
        supplier_status=as_int(
            pick(supplier, "status", "supplierStatus", "supplier_status")
        ),
        credit_rating=pick(supplier, "creditRating", "credit_rating"),
        order_time=as_datetime(pick(raw, "orderTime", "order_time")),
        actual_delivery_date=as_date(
            pick(raw, "actualDeliveryDate", "actual_delivery_date")
        ),
    )


def normalize_order(raw: Any) -> OrderRecord:
    if not isinstance(raw, dict):
        raise ValueError(f"订单响应不是对象: {raw!r}")
    details_raw = (
            raw.get("orderDetail")
            or raw.get("orderDetails")
            or raw.get("details")
            or []
    )
    details = [
        normalize_order_detail(item)
        for item in details_raw
        if isinstance(item, dict)
    ] if isinstance(details_raw, list) else []

    supplier = raw.get("supplier") or {}
    supplier_id = as_int(
        pick(supplier, "id", "supplierId", "supplier_id")
        or pick(raw, "supplierId", "supplier_id")
    )
    supplier_name = pick(
        supplier, "name", "supplierName", "supplier_name"
    ) or pick(raw, "supplierName", "supplier_name")

    return OrderRecord(
        order_id=as_int(pick(raw, "orderId", "order_id", "id")),
        order_number=pick(raw, "orderNumber", "order_number"),
        status=as_int(pick(raw, "status", "orderStatus", "order_status")),
        total_amount=as_decimal(pick(raw, "totalAmount", "total_amount")),
        order_time=as_datetime(pick(raw, "orderTime", "order_time")),
        expected_delivery_date=as_date(
            pick(raw, "expectedDeliveryDate", "expected_delivery_date")
        ),
        actual_delivery_date=as_date(
            pick(raw, "actualDeliveryDate", "actual_delivery_date")
        ),
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        details=details,
    )
