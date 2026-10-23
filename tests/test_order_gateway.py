from agent.procurement.erp_order_gateway import McpErpOrderGateway


def test_extract_order_supports_snake_case_contract_fields():
    order = McpErpOrderGateway._extract_order(
        {
            "order_id": 190,
            "order_number": "P0BCF94565B4B2",
        },
        fallback_number=None,
    )

    assert order.order_id == "190"
    assert order.order_number == "P0BCF94565B4B2"
