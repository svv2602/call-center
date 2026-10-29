"""Tyre properties come from tshina; 1C is the fallback (owner, 2026-10-29).

Runs the real ``_query_tire_rows`` SQL on the SQLite stand of
``test_store_client_tire_item``: brand, model and season of
``tshina_tire_models``, RunFlat / load / speed of ``tire_attributes`` and
ommology of ``product_ommology`` win over the 1C columns; a SKU the site does
not carry keeps the 1C values.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.store_client.client import StoreClient
from tests.unit.test_store_client_tire_item import _CATALOGUE, _SqliteEngine

if TYPE_CHECKING:
    from collections.abc import Callable

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
        "INSERT INTO tire_attributes (sku, load_index, speed_index, season, is_runflat)"
        " VALUES (?, '95', 'Y', 'all_season', 1)",
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


# ── the extended contract (068): size, C/LT and the rest from the site ──────

EXTENDED_SKU = "w-norow-desc-none"  # 1C: 225/45 R18, not commercial


def _extended(**cols: Any) -> Callable[[dict[str, Any]], dict[str, dict[str, Any]]]:
    engine = _stand()
    if cols.pop("one_c_commercial", False):
        engine.db.execute("UPDATE tire_products SET commercial = 1 WHERE sku = ?", (EXTENDED_SKU,))
    engine.db.execute(
        "INSERT INTO tshina_tire_models VALUES (?, 'Matador', 'Sibir Ice 2', 'winter')",
        (_model_id(EXTENDED_SKU),),
    )
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    engine.db.execute(
        f"INSERT INTO tire_attributes (sku, {names}) VALUES (?, {marks})",
        (EXTENDED_SKU, *cols.values()),
    )
    client = StoreClient(base_url="http://localhost", api_key="k", db_engine=engine)

    def run(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = asyncio.run(client._query_tire_rows("ProKoleso", params))
        return {row["id"]: StoreClient._tire_item(row) for row in rows}

    return run


def test_the_site_size_is_searched_and_shown() -> None:
    run = _extended(width=235, profile=40, diameter=18, size="235/40 R18")
    assert EXTENDED_SKU not in run({"width": 225})
    item = run({"width": 235, "profile": 40, "diameter": 18})[EXTENDED_SKU]
    assert item["size"] == "235/40 R18"


def test_commercial_comes_from_the_site_on_an_extended_row() -> None:
    run = _extended(size="225/45 R18C", commercial="C")
    assert EXTENDED_SKU in run({"width": 225, "commercial": True})
    assert EXTENDED_SKU not in run({"width": 225, "commercial": False})


def test_an_older_row_leaves_commercial_to_1c() -> None:
    """No ``size`` yet: NULL ``commercial`` is unknown, not «passenger»."""
    run = _extended(commercial=None, one_c_commercial=True)
    assert EXTENDED_SKU in run({"width": 225, "commercial": True})
    assert EXTENDED_SKU not in run({"width": 225, "commercial": False})


def test_the_sku_brand_wins_over_the_model_brand() -> None:
    run = _extended(size="225/45 R18", brand_title="Matador Rubber")
    assert run({"width": 225})[EXTENDED_SKU]["brand"] == "Matador Rubber"


def test_the_new_properties_reach_the_item() -> None:
    run = _extended(
        size="225/45 R18",
        suv=1,
        electric_car=0,
        tire_insulation="contisilent",
        tire_ply="pr8",
        tire_technology="techsealtech",
    )
    item = run({"width": 225})[EXTENDED_SKU]
    assert item["suv"] is True and "electric_car" not in item
    assert item["noise_insulation"] == "contisilent" and item["ply"] == "pr8"
    assert item["technology"] == ["techsealtech"]
