"""tshina Data API client against a real aiohttp test server (no mocks of the session)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.integrations.tshina_api import (
    TshinaApiClient,
    TshinaApiError,
    format_since,
    parse_server_time,
)

ST = "2026-09-29T02:00:00Z"


def ok(items: list[dict[str, Any]] | None = None, cursor: str | None = None) -> tuple:
    return 200, {"items": items or [], "next_cursor": cursor, "server_time": ST}, {}


class Api:
    """Scripted server: each request pops the next (status, body, headers)."""

    def __init__(self, *responses: tuple) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append(
            {"path": request.path, "query": dict(request.query), "headers": dict(request.headers)}
        )
        status, body, headers = self.responses.pop(0)
        if isinstance(body, str):
            return web.Response(status=status, text=body, headers=headers)
        return web.json_response(body, status=status, headers=headers)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _server(api: Api) -> TestServer:
    app = web.Application()
    app.router.add_get("/api/v1/{tail:.*}", api.handle)
    server = TestServer(app)
    await server.start_server()
    return server


async def _client(server: TestServer, sleeps: Sleeps, **kw: Any) -> TshinaApiClient:
    return TshinaApiClient(str(server.make_url("/")), "tok", sleep=sleeps, clock=lambda: 0.0, **kw)


async def test_bearer_auth_without_basic() -> None:
    api = Api(ok())
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            await client.fetch_page("eu-labels")
    finally:
        await server.close()
    headers = api.requests[0]["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert "X-Api-Token" not in headers


async def test_stand_basic_auth_moves_token_to_x_api_token() -> None:
    api = Api(ok())
    server = await _server(api)
    try:
        async with await _client(server, Sleeps(), basic_user="u", basic_password="p") as client:
            await client.fetch_page("eu-labels")
    finally:
        await server.close()
    headers = api.requests[0]["headers"]
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"u:p").decode()
    assert headers["X-Api-Token"] == "tok"


async def test_walks_cursor_pages_with_since_and_limit() -> None:
    api = Api(ok([{"sku": "1"}], cursor="c1"), ok([{"sku": "2"}]))
    server = await _server(api)
    since = datetime(2026, 9, 28, 5, 0, 0, tzinfo=timezone(timedelta(hours=3)))
    try:
        async with await _client(server, Sleeps()) as client:
            pages = [
                p async for p in client.iter_pages("eu-labels", updated_since=since, limit=1000)
            ]
    finally:
        await server.close()
    assert [p.items for p in pages] == [[{"sku": "1"}], [{"sku": "2"}]]
    first, second = (r["query"] for r in api.requests)
    assert first == {"limit": "1000", "updated_since": "2026-09-28T02:00:00Z"}
    assert second["cursor"] == "c1" and second["updated_since"] == "2026-09-28T02:00:00Z"
    assert api.requests[0]["path"] == "/api/v1/eu-labels"


async def test_limit_is_capped_at_contract_maximum() -> None:
    api = Api(ok())
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            await client.fetch_page("vehicles/tire-sizes", limit=50_000)
    finally:
        await server.close()
    assert api.requests[0]["query"]["limit"] == "5000"
    assert api.requests[0]["path"] == "/api/v1/vehicles/tire-sizes"


async def test_429_waits_retry_after_capped_at_60s() -> None:
    api = Api(
        (429, {"error": "slow down"}, {"Retry-After": "7"}),
        (429, {"error": "slow down"}, {"Retry-After": "600"}),
        ok(),
    )
    server = await _server(api)
    sleeps = Sleeps()
    try:
        async with await _client(server, sleeps) as client:
            page = await client.fetch_page("eu-labels")
    finally:
        await server.close()
    assert page.server_time == ST
    assert len(api.requests) == 3
    assert 7.0 in sleeps.calls and 60.0 in sleeps.calls
    assert max(sleeps.calls) == 60.0


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_retried_three_times_then_fails(status: int) -> None:
    api = Api(*[(status, {"error": "boom"}, {})] * 4)
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            with pytest.raises(TshinaApiError) as err:
                await client.fetch_page("eu-labels")
    finally:
        await server.close()
    assert len(api.requests) == 4
    assert err.value.kind == "http" and err.value.status == status


async def test_5xx_then_success_recovers() -> None:
    api = Api((503, "", {}), (500, {"error": "x"}, {}), ok([{"sku": "1"}]))
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            page = await client.fetch_page("eu-labels")
    finally:
        await server.close()
    assert page.items == [{"sku": "1"}]
    assert len(api.requests) == 3


@pytest.mark.parametrize(
    ("status", "kind"), [(401, "auth"), (403, "auth"), (400, "bad_request"), (404, "http")]
)
async def test_client_errors_fail_at_once_with_server_text(status: int, kind: str) -> None:
    api = Api((status, {"error": "updated_since needs a zone"}, {}), ok())
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            with pytest.raises(TshinaApiError) as err:
                await client.fetch_page("eu-labels")
    finally:
        await server.close()
    assert len(api.requests) == 1
    assert err.value.kind == kind and err.value.status == status
    assert "updated_since needs a zone" in str(err.value)


async def test_network_error_retried_then_fails() -> None:
    api = Api()
    server = await _server(api)
    url = str(server.make_url("/"))
    await server.close()  # nothing listens any more
    sleeps = Sleeps()
    async with TshinaApiClient(url, "tok", sleep=sleeps, clock=lambda: 0.0) as client:
        with pytest.raises(TshinaApiError) as err:
            await client.fetch_page("eu-labels")
        assert client.requests_sent == 4
    assert err.value.kind == "network"


async def test_requests_spaced_to_five_per_second() -> None:
    api = Api(ok(cursor="a"), ok(cursor="b"), ok())
    server = await _server(api)
    sleeps = Sleeps()
    try:
        async with await _client(server, sleeps) as client:
            _ = [p async for p in client.iter_pages("eu-labels")]
    finally:
        await server.close()
    assert sleeps.calls == [pytest.approx(0.2), pytest.approx(0.2)]


@pytest.mark.parametrize(
    "body",
    [
        {"items": [], "next_cursor": None},
        {"items": [], "next_cursor": None, "server_time": "2026-09-29T02:00:00"},
        {"items": "x", "next_cursor": None, "server_time": ST},
        {"items": [1], "next_cursor": None, "server_time": ST},
        "not json",
    ],
)
async def test_contract_breach_is_a_protocol_error(body: Any) -> None:
    api = Api((200, body, {}))
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            with pytest.raises(TshinaApiError) as err:
                await client.fetch_page("eu-labels")
    finally:
        await server.close()
    assert err.value.kind == "protocol"


async def test_repeating_cursor_stops_the_walk() -> None:
    api = Api(ok(cursor="same"), ok(cursor="same"))
    server = await _server(api)
    try:
        async with await _client(server, Sleeps()) as client:
            with pytest.raises(TshinaApiError, match="repeats"):
                _ = [p async for p in client.iter_pages("eu-labels")]
    finally:
        await server.close()


@pytest.mark.parametrize(("url", "token"), [("", "tok"), ("http://x", ""), ("  ", " ")])
def test_client_refuses_without_url_or_token(url: str, token: str) -> None:
    with pytest.raises(ValueError):
        TshinaApiClient(url, token)


def test_since_and_server_time_formats() -> None:
    assert format_since(datetime(2026, 9, 29, 2, 0, 0, 123, tzinfo=UTC)) == "2026-09-29T02:00:00Z"
    with pytest.raises(ValueError):
        format_since(datetime(2026, 9, 29))
    assert parse_server_time(ST) == datetime(2026, 9, 29, 2, 0, tzinfo=UTC)
