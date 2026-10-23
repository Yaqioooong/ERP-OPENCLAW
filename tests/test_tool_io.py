import httpx
import pytest

from mcp_server.contracts import ToolErrorCode
from mcp_server.tool_io import request_json


class FakeRequest:
    headers = {"x-request-id": "req-test"}


class FakeRequestContext:
    def __init__(self, client):
        self.lifespan_context = {"http_client": client}
        self.request = FakeRequest()


class FakeContext:
    def __init__(self, client):
        self.request_context = FakeRequestContext(client)


def make_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )


@pytest.mark.anyio
async def test_not_found_ok_returns_none():
    async def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="order_get",
            method="GET",
            path="/orders/get/1",
            not_found_ok=True,
        )
    assert data is None
    assert error is None


@pytest.mark.anyio
async def test_http_404_is_not_found_error():
    async def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="order_create",
            method="POST",
            path="/orders/create",
            json_body={},
        )
    assert data is None
    assert error is not None
    assert error.error.code == ToolErrorCode.NOT_FOUND


@pytest.mark.anyio
async def test_timeout_is_retryable():
    async def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="part_search",
            method="GET",
            path="/parts/search",
            params={"name": "x"},
        )
    assert data is None
    assert error.error.code == ToolErrorCode.TIMEOUT
    assert error.error.retryable is True


@pytest.mark.anyio
async def test_duplicate_business_code_is_conflict():
    async def handler(request):
        return httpx.Response(
            200,
            json={"code": "ORDER_DUPLICATE", "message": "订单号已存在"},
        )

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="order_create",
            method="POST",
            path="/orders/create",
            json_body={},
        )
    assert data is None
    assert error.error.code == ToolErrorCode.CONFLICT
    assert error.error.details["duplicate"] is True

@pytest.mark.anyio
async def test_connect_error_is_upstream_unavailable():
    async def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="part_search",
            method="GET",
            path="/parts/search",
            params={"name": "x"},
        )

    assert data is None
    assert error is not None
    assert error.error.code == ToolErrorCode.UPSTREAM_UNAVAILABLE
    assert error.error.retryable is True
    assert error.error.details["exception_type"] == "ConnectError"


@pytest.mark.anyio
async def test_invalid_json_is_upstream_unavailable():
    async def handler(request):
        return httpx.Response(
            200,
            content=b"<html>gateway error</html>",
            headers={"content-type": "text/html"},
        )

    async with make_client(handler) as client:
        data, error = await request_json(
            ctx=FakeContext(client),
            tool="part_search",
            method="GET",
            path="/parts/search",
            params={"name": "x"},
        )

    assert data is None
    assert error is not None
    assert error.error.code == ToolErrorCode.UPSTREAM_UNAVAILABLE
    assert error.error.details["reason"] == "INVALID_JSON"
    assert error.error.details["status"] == 200


class BrokenClient:
    async def request(self, *args, **kwargs):
        raise ValueError("bad url")


@pytest.mark.anyio
async def test_programming_error_is_internal_error():
    data, error = await request_json(
        ctx=FakeContext(BrokenClient()),
        tool="part_search",
        method="GET",
        path="/parts/search",
    )

    assert data is None
    assert error is not None
    assert error.error.code == ToolErrorCode.INTERNAL_ERROR
    assert error.error.retryable is False
    assert error.error.details["exception_type"] == "ValueError"
