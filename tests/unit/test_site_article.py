"""The storefront article is the 1C code without its zeros (call 9b0fcd8b, 2026-10-29).

prokoleso.ua shows «Артикул: 53253» on the card of «00000053253»; the bot
dictated the eleven-digit code as the article for the site.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import create_autospec, patch

import pytest

from src.agent.network_policy import NetworkPolicy
from src.agent.tool_result_compressor import compress_tool_result
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client.client import StoreClient, site_article, sku_from_article


@pytest.mark.parametrize(
    ("sku", "article"),
    [
        ("00000053253", "53253"),
        ("53253", "53253"),
        ("00000000000", None),
        ("AB-1", None),
        ("", None),
    ],
)
def test_site_article(sku: str, article: str | None) -> None:
    assert site_article(sku) == article


@pytest.mark.parametrize(
    ("code", "sku"),
    [
        ("53253", "00000053253"),
        (" 63910 ", "00000063910"),
        ("00000053253", "00000053253"),
        ("X1", "X1"),
    ],
)
def test_sku_from_article(code: str, sku: str) -> None:
    assert sku_from_article(code) == sku


def test_sales_items_carry_the_article() -> None:
    result = {"total": 1, "items": [{"id": "00000063910", "brand": "Sportrak", "price": 3118}]}
    out = json.loads(compress_tool_result("search_tires", result, sales_enabled=True))
    assert out["items"][0]["article"] == "63910"
    off = json.loads(compress_tool_result("search_tires", result, sales_enabled=False))
    assert "article" not in off["items"][0]


async def _check(args: dict[str, Any]) -> Any:
    store = create_autospec(StoreClient, instance=True)
    store.check_availability.return_value = {"available": True}
    session = CallSession(uuid.uuid4())
    router = _build_tool_router(
        session, store_client=store, network_policy=NetworkPolicy(sales_enabled=True)
    )
    with patch("src.main._call_logger", None):
        await router.execute("check_availability", args)
    return store.check_availability.await_args


@pytest.mark.asyncio
async def test_availability_takes_the_storefront_article() -> None:
    call = await _check({"product_id": "53253"})
    assert call.args[0] == "00000053253"


@pytest.mark.asyncio
async def test_a_bare_article_in_query_is_the_product() -> None:
    call = await _check({"query": "63910"})
    assert call.args[:2] == ("00000063910", "")


@pytest.mark.asyncio
async def test_a_text_query_is_untouched() -> None:
    call = await _check({"query": "Blizzak 6"})
    assert call.args[:2] == ("", "Blizzak 6")
