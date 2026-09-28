"""Vehicle model lookup: a model as heard by STT reaches the catalogue row.

Wave 1-C (sales-structural 2026-09-28). Prod probe with the real
``StoreClient.get_vehicle_tire_sizes``: «Мерседес ГЛА/ГЛЦ/глк/ГЛС/гле»,
«джі ел ей», «гла клас», «GLA 200» → not found; «GLE» → «GLE AMG» instead of
GLE-Class; alias «Глк-клас» pointed at GLC-Class (GLK-Class collision).

- ``model_query_variants`` — pure normalizer (corpus below);
- ``pick_prefix_model`` — the base model, never an AMG/Coupe sibling, and no
  guess when only siblings exist;
- alias generation — letter codes keep letter sounds (GLC ≠ GLK), an alias
  shared by two models of a brand is not emitted for both;
- ``_find_vehicle_model`` / ``get_vehicle_tire_sizes`` through a fake engine
  that answers SQL from an in-memory catalogue (no bare AsyncMock).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest

from scripts.fix_vehicle_aliases import intra_brand_collisions, plan_alias_fix
from scripts.generate_aliases import (
    _build_model_alias_rows,
    drop_model_alias_collisions,
)
from src.agent.vehicle_model_normalizer import (
    model_query_variants,
    pick_prefix_model,
    prefix_like_pattern,
)
from src.agent.vehicle_translit import generate_model_aliases, normalize_alias
from src.store_client.client import StoreClient

# ── normalizer corpus ────────────────────────────────────────────────────

# heard → the Latin key the catalogue lookup must be offered
LATIN_CORPUS: dict[str, str] = {
    # Cyrillic abbreviation, by letter sound
    "ГЛА": "gla",
    "ГЛЦ": "glc",
    "глк": "glk",
    "ГЛС": "gls",
    "гле": "gle",
    "Х5": "x5",
    "мл": "ml",
    # letters spelled one by one, ua + ru
    "джі ел ей": "gla",
    "джи эл эй": "gla",
    "джі ел сі": "glc",
    "джи эл си": "glc",
    "джі ел і": "gle",
    "джи эл эс": "gls",
    "джі ел кей": "glk",
    # a letter name + a number (words or digits, glued or not)
    "ікс п'ять": "x5",
    "икс пять": "x5",
    "ікс5": "x5",
    "ку п'ять": "q5",
    "Ку5": "q5",
    "ку сім": "q7",
    "ей шість": "a6",
    "эй 4": "a4",
    # class / series words and the engine go away
    "гла клас": "gla",
    "гла-класс": "gla",
    "GLA 200": "gla",
    "E 220d": "e",
    "е клас": "e",
    "джі клас": "g",
    "ес клас": "s",
    "ц-класс": "c",
    "S 500": "s",
    "3 серія": "3",
    "5 серии": "5",
    "Camry 2.5": "camry",
    "RAV 4": "rav4",
    "рав 4": "rav4",
    "джі ел ей клас 200": "gla",
}


@pytest.mark.parametrize(("heard", "key"), sorted(LATIN_CORPUS.items()))
def test_heard_model_offers_catalogue_key(heard: str, key: str) -> None:
    assert key in model_query_variants(heard)


@pytest.mark.parametrize("heard", ["GLE", "Land Cruiser 200", "Sportage", "glc", "X5", "RAV4", ""])
def test_already_catalogue_form_has_no_extra_variant(heard: str) -> None:
    """The as-is form is never repeated and a plain name grows nothing."""
    assert model_query_variants(heard) == []


def test_cyrillic_word_alias_survives_stripping() -> None:
    """«тігуан 2.0» — the Cyrillic form stays first so the alias still hits."""
    assert model_query_variants("Тігуан 2.0")[0] == "тігуан"


def test_letters_are_not_read_as_words() -> None:
    """A spelled run is one code, never letter names left as text."""
    for heard in ("джі ел ей", "джи эл си", "ікс п'ять"):
        for v in model_query_variants(heard):
            assert not re.search(r"[а-яіїє]", v) or v == heard


def test_three_digit_model_number_kept_without_code() -> None:
    """«Land Cruiser 200», Audi «100»: a number is an engine only after a code."""
    assert "land cruiser" not in model_query_variants("Land Cruiser 200")
    assert model_query_variants("200") == []


def test_like_pattern_escapes_wildcards() -> None:
    assert prefix_like_pattern("a_b%") == "a\\_b\\%%"


# ── prefix fallback: base model ──────────────────────────────────────────


def _rows(*names: str) -> list[dict[str, Any]]:
    return [{"id": i + 1, "name": n} for i, n in enumerate(names)]


def test_prefix_takes_class_base_not_amg_ordered_first() -> None:
    rows = _rows("GLE AMG", "GLE-Class AMG", "GLE-Class Coupe", "GLE-Class", "GLE Coupe(C292)")
    row, ambiguous = pick_prefix_model("gle", rows)
    assert row is not None and row["name"] == "GLE-Class"
    assert ambiguous is False


def test_prefix_series_base() -> None:
    rows = _rows("3 (E46)", "3 GT (F34)", "3 Series")
    row, _ = pick_prefix_model("3", rows)
    assert row is not None and row["name"] == "3 Series"


def test_prefix_same_name_duplicates_take_first_row() -> None:
    """Rows arrive ordered by kits: the first ``GLA-Class`` is the populated one."""
    rows = [
        {"id": 2325, "name": "GLA-Class"},
        {"id": 2324, "name": "GLA-Class"},
        {"id": 2327, "name": "GLA-Class AMG"},
    ]
    row, ambiguous = pick_prefix_model("gla", rows)
    assert row is not None and row["id"] == 2325 and not ambiguous


def test_prefix_only_siblings_is_ambiguous_not_a_guess() -> None:
    row, ambiguous = pick_prefix_model("gle", _rows("GLE AMG", "GLE Coupe(C292)"))
    assert row is None and ambiguous is True


def test_prefix_single_plain_name_is_taken() -> None:
    row, _ = pick_prefix_model("prius", _rows("Prius AMG", "Prius Prime"))
    assert row is not None and row["name"] == "Prius Prime"


def test_prefix_ignores_non_boundary_match() -> None:
    """«gl» must not become GLA/GLC by string prefix."""
    row, ambiguous = pick_prefix_model("gl", _rows("GLA-Class", "GLC-Class"))
    assert row is None and ambiguous is False


# ── alias generation: no intra-brand collisions ──────────────────────────


def _alias_keys(name: str) -> set[str]:
    return {normalize_alias(a) for a, _ in generate_model_aliases(name)}


@pytest.mark.parametrize(
    ("a", "b"),
    [("GLC-Class", "GLK-Class"), ("CLC-Class", "CLK-Class"), ("SLC-Class", "SLK-Class")],
)
def test_letter_code_aliases_do_not_collide(a: str, b: str) -> None:
    assert _alias_keys(a).isdisjoint(_alias_keys(b))


def test_glk_owns_its_cyrillic_alias() -> None:
    assert "глк-клас" in _alias_keys("GLK-Class")
    assert "глц-клас" in _alias_keys("GLC-Class")
    assert "глк-клас" not in _alias_keys("GLC-Class")


MERCEDES_AND_BMW = [
    {"id": 2324, "brand_id": 85, "name": "GLA-Class", "kits": 6},
    {"id": 2325, "brand_id": 85, "name": "GLA-Class", "kits": 170},
    {"id": 2329, "brand_id": 85, "name": "GLC-Class", "kits": 137},
    {"id": 2341, "brand_id": 85, "name": "GLK-Class", "kits": 159},
    {"id": 2336, "brand_id": 85, "name": "GLE-Class", "kits": 46},
    {"id": 2334, "brand_id": 85, "name": "GLE AMG", "kits": 34},
    {"id": 228, "brand_id": 11, "name": "X1", "kits": 50},
    {"id": 229, "brand_id": 11, "name": "iX1", "kits": 5},
]


def test_generated_rows_have_no_intra_brand_collision() -> None:
    rows = _build_model_alias_rows(MERCEDES_AND_BMW)
    assert intra_brand_collisions(rows) == {}


def test_generated_duplicate_name_gets_aliases_once_on_populated_model() -> None:
    rows = _build_model_alias_rows(MERCEDES_AND_BMW)
    gla = {r["model_id"] for r in rows if r["alias_normalized"] == "гла-клас"}
    assert gla == {2325}


def test_hand_alias_wins_collision_over_translit() -> None:
    """«ікс1»: hand-curated for X1, char translit for iX1 → X1 keeps it."""
    rows = _build_model_alias_rows(MERCEDES_AND_BMW)
    owners = {r["model_id"] for r in rows if r["alias_normalized"] == "ікс1"}
    assert owners == {228}


def test_tied_collision_is_dropped_for_both() -> None:
    rows = [
        {"alias_normalized": "глк-клас", "brand_id": 85, "model_id": 1, "source": "auto_translit"},
        {"alias_normalized": "глк-клас", "brand_id": 85, "model_id": 2, "source": "auto_translit"},
        {"alias_normalized": "глк-клас", "brand_id": 9, "model_id": 3, "source": "auto_translit"},
    ]
    kept, dropped = drop_model_alias_collisions(rows, {1: "GLC-Class", 2: "GLK-Class", 3: "Other"})
    assert {r["model_id"] for r in kept} == {3}
    assert {r["model_id"] for r in dropped} == {1, 2}


def test_fix_plan_deletes_collision_and_never_manual() -> None:
    current = [
        {"id": 1, "alias_normalized": "глк-клас", "brand_id": 85, "model_id": 2329,
         "source": "auto_translit"},
        {"id": 2, "alias_normalized": "глк-клас", "brand_id": 85, "model_id": 2341,
         "source": "auto_translit"},
        {"id": 3, "alias_normalized": "моя гла", "brand_id": 85, "model_id": 2325,
         "source": "manual"},
    ]  # fmt: skip
    desired = _build_model_alias_rows(MERCEDES_AND_BMW)
    to_delete, to_add = plan_alias_fix(current, desired)
    assert {r["id"] for r in to_delete} == {1}
    added = {(r["alias_normalized"], r["model_id"]) for r in to_add}
    assert ("глц-клас", 2329) in added
    assert ("глк-клас", 2341) not in added  # already there


# ── _find_vehicle_model through a fake engine ────────────────────────────


@dataclass
class _Model:
    id: int
    brand_id: int
    name: str
    kits: int


BRANDS = {85: "Mercedes", 11: "BMW"}
MODELS = [
    _Model(2324, 85, "GLA-Class", 6),
    _Model(2325, 85, "GLA-Class", 170),
    _Model(2327, 85, "GLA-Class AMG", 37),
    _Model(2326, 85, "GLA-Class (X156)", 9),
    _Model(2329, 85, "GLC-Class", 137),
    _Model(2328, 85, "GLC-Class", 3),
    _Model(2330, 85, "GLC-Class AMG", 53),
    _Model(2331, 85, "GLC-Class Coupe", 81),
    _Model(2341, 85, "GLK-Class", 159),
    _Model(2342, 85, "GLK-Class (X204)", 24),
    _Model(2334, 85, "GLE AMG", 34),
    _Model(2336, 85, "GLE-Class", 46),
    _Model(2337, 85, "GLE-Class AMG", 35),
    _Model(2338, 85, "GLE-Class Coupe", 46),
    _Model(2335, 85, "GLE Coupe(C292)", 6),
    _Model(2343, 85, "GLS-Class", 61),
    _Model(2320, 85, "GL-Class", 152),
    _Model(236, 11, "X5", 80),
    _Model(237, 11, "X5 M", 10),
]
# brand aliases as on prod; model aliases as on prod before the fix (only
# full Cyrillic class forms, «глк-клас» colliding)
ALIASES = [
    ("мерседес", 85, None),
    ("мерс", 85, None),
    ("гла-клас", 85, 2324),
    ("гла-клас", 85, 2325),
    ("глк-клас", 85, 2329),
    ("глк-клас", 85, 2341),
    ("гле-клас", 85, 2336),
]


def _trgm(s: str) -> set[str]:
    out: set[str] = set()
    for w in re.split(r"[^\w]+", s.lower()):
        if w:
            p = f"  {w} "
            out |= {p[i : i + 3] for i in range(len(p) - 2)}
    return out


def _similarity(a: str, b: str) -> float:
    ta, tb = _trgm(a), _trgm(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


class _Row(tuple):
    """SQLAlchemy Row stand-in: positional access and ``_mapping``."""

    _mapping: dict[str, Any]

    @classmethod
    def of(cls, mapping: dict[str, Any]) -> _Row:
        row = cls(mapping.values())
        row._mapping = mapping
        return row


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(_Row.of(r) for r in self._rows)


def _ordered(models: list[_Model], sql: str) -> list[_Model]:
    """Kits order only when the SQL asks for it; else catalogue order (the
    unpopulated duplicate ``GLA-Class`` 2324 comes first, as ``first()`` on
    prod may return)."""
    if "ORDER BY (SELECT COUNT(*) FROM vehicle_kits" in sql:
        return sorted(models, key=lambda m: (-m.kits, m.id))
    return list(models)


class _CatalogueConn:
    """Answers the vehicle lookup SQL from ``BRANDS`` / ``MODELS`` / ``ALIASES``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _CatalogueConn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(query).split())
        p = dict(params or {})
        self.calls.append((sql, p))
        return _Result(self._answer(sql, p))

    def _answer(self, sql: str, p: dict[str, Any]) -> list[dict[str, Any]]:
        if "FROM vehicle_brands WHERE LOWER(name) = LOWER(:name)" in sql:
            return [
                {"id": i, "name": n} for i, n in BRANDS.items() if n.lower() == p["name"].lower()
            ]
        if "FROM vehicle_brands WHERE similarity" in sql:
            best = [
                (_similarity(n, p["name"]), i, n) for i, n in BRANDS.items()
                if _similarity(n, p["name"]) > 0.3
            ]  # fmt: skip
            return [{"id": i, "name": n} for _, i, n in sorted(best, reverse=True)[:1]]
        if "FROM vehicle_aliases va" in sql and "va.brand_id = :bid" in sql:
            hits = [
                {"model_id": mid, "name": next(m.name for m in MODELS if m.id == mid)}
                for a, b, mid in ALIASES
                if a == p["norm"] and b == p["bid"] and mid is not None
            ]
            return hits[:2]
        if "FROM vehicle_aliases va" in sql:
            return [
                {"brand_id": b, "brand_name": BRANDS[b], "model_id": mid, "model_name": None,
                 "source": "auto_translit"}
                for a, b, mid in ALIASES if a == p["norm"]
            ]  # fmt: skip
        if "FROM vehicle_models" in sql and "LOWER(name) = LOWER(:name)" in sql:
            ms = [
                m for m in MODELS if m.brand_id == p["bid"] and m.name.lower() == p["name"].lower()
            ]
            return [{"id": m.id, "name": m.name} for m in _ordered(ms, sql)]
        if "FROM vehicle_models" in sql and "LIKE :pattern" in sql:
            prefix = re.sub(r"\\(.)", r"\1", p["pattern"][:-1])
            ms = [m for m in MODELS if m.brand_id == p["bid"] and m.name.lower().startswith(prefix)]
            return [{"id": m.id, "name": m.name} for m in _ordered(ms, sql)]
        if "FROM vehicle_models" in sql and "similarity" in sql:
            scored = [
                (_similarity(m.name, p["name"]), -len(m.name), m) for m in MODELS
                if m.brand_id == p["bid"] and _similarity(m.name, p["name"]) > 0.3
            ]  # fmt: skip
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            return [{"id": t[2].id, "name": t[2].name} for t in scored[:1]]
        if "SELECT DISTINCT k.year" in sql:
            return [{"year": 2020}]
        if "FROM vehicle_tire_sizes" in sql:
            return [
                {"width": 235, "height": 50, "diameter": 19, "type": 1, "axle": 0,
                 "kit_id": 1, "axle_group": None}
            ]  # fmt: skip
        raise AssertionError(f"unexpected SQL: {sql}")


class _CatalogueEngine:
    def __init__(self) -> None:
        self.conn = _CatalogueConn()

    def connect(self) -> _CatalogueConn:
        return self.conn


async def _find(model: str, brand_id: int = 85) -> Any:
    return await StoreClient._find_vehicle_model(_CatalogueConn(), brand_id, model)


def test_fake_trigram_reproduces_prod_gle_amg() -> None:
    """Sanity: the fake's trigram picks «GLE AMG» for «GLE», as prod did."""
    scored = sorted(
        (m for m in MODELS if m.brand_id == 85),
        key=lambda m: (_similarity(m.name, "GLE"), -len(m.name)),
        reverse=True,
    )
    assert scored[0].name == "GLE AMG"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("heard", "expected_id"),
    [
        ("ГЛА", 2325),
        ("гла клас", 2325),
        ("джі ел ей", 2325),
        ("GLA 200", 2325),
        ("GLA", 2325),
        ("GLA-Class", 2325),
        ("ГЛЦ", 2329),
        ("джи эл си", 2329),
        ("глк", 2341),
        ("ГЛС", 2343),
        ("гле", 2336),
        ("GLE", 2336),
        ("GL", 2320),
    ],
)
async def test_heard_model_resolves_to_base_row(heard: str, expected_id: int) -> None:
    row = await _find(heard)
    assert row is not None, heard
    assert row["id"] == expected_id, (heard, row)


@pytest.mark.asyncio
async def test_bmw_letter_number_spelled() -> None:
    row = await _find("ікс п'ять", brand_id=11)
    assert row is not None and row["name"] == "X5"


@pytest.mark.asyncio
async def test_ambiguous_siblings_return_none_not_trigram() -> None:
    """Only siblings under the key → no guess (trigram would pick one)."""
    global MODELS
    saved = MODELS
    MODELS = [m for m in saved if m.name not in ("GLE-Class",)]
    try:
        conn = _CatalogueConn()
        row = await StoreClient._find_vehicle_model(conn, 85, "GLE")
        assert row is None
        assert not any("similarity" in sql for sql, _ in conn.calls)
    finally:
        MODELS = saved


@pytest.mark.asyncio
async def test_unknown_model_still_reaches_trigram() -> None:
    conn = _CatalogueConn()
    assert await StoreClient._find_vehicle_model(conn, 85, "Zzzz") is None
    assert any("similarity" in sql for sql, _ in conn.calls)


@pytest.mark.asyncio
async def test_exact_duplicate_name_prefers_populated_model() -> None:
    row = await _find("GLA-Class")
    assert row["id"] == 2325


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("brand", "model", "expected"),
    [
        ("Мерседес", "ГЛА", "GLA-Class"),
        ("Мерс", "гла клас", "GLA-Class"),
        ("Mercedes", "GLE", "GLE-Class"),
        ("Мерседес", "джі ел ей", "GLA-Class"),
        ("Mercedes", "GLA 200", "GLA-Class"),
    ],
)
async def test_get_vehicle_tire_sizes_mercedes(brand: str, model: str, expected: str) -> None:
    client = StoreClient(base_url="http://x", api_key="k", db_engine=_CatalogueEngine())
    result = await client.get_vehicle_tire_sizes(brand=brand, model=model)
    assert result["found"] is True, result
    assert result["brand"] == "Mercedes"
    assert result["model"] == expected
