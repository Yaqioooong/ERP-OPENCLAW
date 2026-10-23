from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mcp_server.mcp_tools_bean import (
    OrderCreateInput,
    OrderDetailItem,
    OrderGetInput,
    OrderSearchInput,
    OrderUpdateInput,
)


def test_order_detail_subtotal_must_match():
    with pytest.raises(ValidationError):
        OrderDetailItem(
            partId=1,
            quantity=2,
            unitPrice=Decimal("10.00"),
            subtotal=Decimal("30.00"),
        )


def test_create_order_requires_detail_and_total_matches():
    with pytest.raises(ValidationError):
        OrderCreateInput(orderDetail=[])

    with pytest.raises(ValidationError):
        OrderCreateInput(
            totalAmount=Decimal("100.00"),
            orderDetail=[
                OrderDetailItem(
                    partId=1,
                    quantity=2,
                    unitPrice=Decimal("10.00"),
                )
            ],
        )


def test_update_order_requires_at_least_one_field():
    with pytest.raises(ValidationError):
        OrderUpdateInput()


def test_search_date_range():
    with pytest.raises(ValidationError):
        OrderSearchInput(
            startDate=date(2026, 10, 2),
            endDate=date(2026, 10, 1),
        )


def test_order_get_exactly_one_key():
    with pytest.raises(ValidationError):
        OrderGetInput()
    with pytest.raises(ValidationError):
        OrderGetInput(order_id=1, order_number="P020261022ABC")
    assert OrderGetInput(order_id=1).order_id == 1