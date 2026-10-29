"""Tyre properties come from tshina; 1C is the fallback (owner, 2026-10-29).

Runs the real ``_query_tire_rows`` SQL on the SQLite stand of
``test_store_client_tire_item``: brand, model and season of
``tshina_tire_models``, RunFlat / load / speed of ``tire_attributes`` and
ommology of ``product_ommology`` win over the 1C columns; a SKU the site does
not carry keeps the 1C values.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.store_client.client import StoreClient
from tests.unit.test_store_client_tire_item import _CATALOGUE, _SqliteEngine

#: model_id of the stand = index in ``_CATALOGUE``
ON_SITE = "s-norow-plain"  # «Continental PC7», 1C season summer
OFF_SITE = "x-norow-desc-xl"  # «Kumho PS71 Extra Load»: no tshina rows at all


def _model_id(sku: str) -> int:
    return next(i for i, row in enumerate(_CATALOGUE) if row[0] == sku)


def _stand() -> _SqliteEngine:
    engine = _SqliteEngine()
    db = engine.db
    db.execute(
        "INSERT INTO tshina_tire_models VALUES (?, 'Continental', 'PremiumContact 7', 'all_season')",
        (_model_id(ON_SITE),),
    )
    db.execute(
        "INSERT INTO tire_attributes VALUES (?, NULL, NULL, '95', 'Y', 'all_season', 1)",
        (ON_SITE,),
    )
    db.execute("INSERT INTO product_ommology VALUES (?, 'MO')", (ON_SITE,))
    # 1C spells brands in capitals; the caller hears the site's spelling
    db.execute(
        "UPDATE tire_models SET manufacturer = 'CONTINENTAL' WHERE id = ?", (_model_id(ON_SITE),)
    )
    db.execute(
        "UPDATE tire_products SET load_rating = '99', speed_rating = 'Y' WHERE sku = ?",
        (OFF_SITE,),
    )
    return engine


def _items(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=_stand())
    rows = asyncio.run(client._query_tire_rows("ProKoleso", {"width": 225, **params}))
    return {row["id"]: StoreClient._tire_item(row) for row in rows}


def test_the_site_names_brand_model_and_season() -> None:
    item = _items({})[ON_SITE]
    assert (item["brand"], item["model"], item["season"]) == (
        "Continental",
        "PremiumContact 7",
        "all_season",
    )


def test_a_sku_off_the_site_keeps_the_1c_values() -> None:
    item = _items({})[OFF_SITE]
    assert item["brand"] == "Kumho" and item["season"] == "summer"
    assert (item["load_index"], item["speed_index"]) == ("99", "Y")


def test_the_season_filter_reads_the_site_season() -> None:
    assert ON_SITE in _items({"season": "all_season"})
    assert ON_SITE not in _items({"season": "summer"})
    assert OFF_SITE in _items({"season": "summer"})


def test_runflat_follows_the_attribute() -> None:
    assert _items({})[ON_SITE].get("runflat") is True
    assert set(_items({"runflat": True})) == {ON_SITE}


def test_brand_matches_either_spelling() -> None:
    assert ON_SITE in _items({"brand": "continental"})
    assert OFF_SITE in _items({"brand": "KUMHO"})


def test_ommology_codes_reach_the_item() -> None:
    items = _items({})
    assert items[ON_SITE]["ommology"] == ["MO"]
    assert "ommology" not in items[OFF_SITE]
