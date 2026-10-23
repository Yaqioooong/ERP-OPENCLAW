from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from pydantic import Field, model_validator

from mcp_server.contracts import ContractModel

MONEY = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


class PartSearchInput(ContractModel):
    name: str = Field(..., min_length=1, max_length=100, description="零件名称，模糊查询")


class PartQueryInput(ContractModel):
    current: int = Field(1, ge=1, description="页码，从1开始")
    size: int = Field(10, ge=1, le=200, description="每页大小")
    name: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=50)
    supplierId: int | None = Field(None, ge=1)


class PartGetInput(ContractModel):
    part_id: int = Field(..., ge=1, description="零件 ID")


class SupplierQueryInput(ContractModel):
    name: str = Field(..., min_length=1, max_length=100)


class SupplierGetInput(ContractModel):
    supplier_id: int = Field(..., ge=1)


class InventorySearchInput(ContractModel):
    current: int = Field(1, ge=1)
    size: int = Field(10, ge=1, le=200)
    partName: str | None = Field(None, max_length=100)
    warehouseLocation: str | None = Field(None, max_length=100)


class OrderDetailItem(ContractModel):
    partId: int = Field(..., ge=1)
    quantity: int = Field(..., ge=1)
    unitPrice: Decimal = Field(..., ge=0)
    subtotal: Decimal | None = Field(None, ge=0)
    remark: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def check_subtotal(self):
        expected = q2(Decimal(self.quantity) * self.unitPrice)
        if self.subtotal is not None and q2(self.subtotal) != expected:
            raise ValueError(
                f"subtotal 不一致：quantity*unitPrice={expected}, "
                f"subtotal={self.subtotal}"
            )
        return self


class OrderCreateInput(ContractModel):
    orderNumber: str | None = Field(
        None, min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"
    )
    totalAmount: Decimal | None = Field(None, ge=0)
    status: int | None = Field(None, ge=1, le=5)
    orderTime: str | None = Field(None, max_length=40)
    expectedDeliveryDate: date | None = None
    actualDeliveryDate: date | None = None
    createdBy: int | None = Field(None, ge=1)
    remark: str | None = Field(None, max_length=500)
    orderDetail: list[OrderDetailItem] = Field(..., min_length=1)

    @model_validator(mode="after")
    def check_total(self):
        if self.totalAmount is None:
            return self
        expected = q2(sum((item.subtotal or item.quantity * item.unitPrice for item in self.orderDetail), Decimal("0")))
        if q2(self.totalAmount) != expected:
            raise ValueError(f"totalAmount 不一致：{self.totalAmount} != {expected}")
        return self


class OrderUpdateInput(ContractModel):
    orderNumber: str | None = Field(
        None, min_length=6, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"
    )
    totalAmount: Decimal | None = Field(None, ge=0)
    status: int | None = Field(None, ge=1, le=5)
    orderTime: str | None = Field(None, max_length=40)
    expectedDeliveryDate: date | None = None
    actualDeliveryDate: date | None = None
    createdBy: int | None = Field(None, ge=1)
    remark: str | None = Field(None, max_length=500)
    orderDetail: list[OrderDetailItem] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def at_least_one_field(self):
        values = self.model_dump(exclude_none=True)
        if not values:
            raise ValueError("更新订单至少需要提供一个字段")
        return self


class OrderSearchInput(ContractModel):
    partName: str | None = Field(None, max_length=100)
    startDate: date | None = None
    endDate: date | None = None

    @model_validator(mode="after")
    def check_range(self):
        if self.startDate and self.endDate and self.startDate > self.endDate:
            raise ValueError("startDate 不能晚于 endDate")
        return self


class OrderGetInput(ContractModel):
    order_id: int | None = Field(None, ge=1)
    order_number: str | None = Field(None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.order_id is None) == (self.order_number is None):
            raise ValueError("order_id 和 order_number 必须且只能提供一个")
        return self
