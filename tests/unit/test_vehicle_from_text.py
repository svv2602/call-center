"""The car in the caller's words reaches `search_disks` (wave 2-J, 2026-09-28).

Goldset №3 `disk_fit_by_car_verdict`: «потрібні литі диски шістнадцятий радіус
на Шкоду Октавію 2018» — the `disk_intent` substitution took the car only from
`get_vehicle_tire_sizes` arguments, so a `search_tires` / knowledge-base call
replaced by `search_disks` carried no car and no fitment verdict.

- `vehicle_words` / `word_forms` — the pure half (stop words, year, cases);
- `StoreClient.resolve_vehicle_text` through a fake engine answering the
  vehicle SQL from an in-memory catalogue (no bare AsyncMock): the brand every
  car word agrees on, the model after it, never a guess;
- wiring: the substitution passes the utterance as ``vehicle_text`` and
  `search_disks` reads the car out of it.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from dataclasses import dataclass
from typing import Any

import pytest

from src.agent.disk_intent import (
    DiskToolRedirect,
    run_disk_substitution,
    vehicle_words,
    word_forms,
)
from src.store_client.client import StoreClient

OCTAVIA = "потрібні литі диски шістнадцятий радіус на Шкоду Октавію 2018"

# ── fake catalogue ───────────────────────────────────────────────────────


@dataclass
class _Model:
    id: int
    brand_id: int
    name: str


BRANDS = {
    70: "Skoda",
    85: "Mercedes",
    30: "Fiat",
    31: "Tank",
    5: "Renault",
    6: "Dacia",
    7: "Suzuki",
    100: "ВАЗ",
    101: "Lada",
}
MODELS = [
    _Model(701, 70, "Octavia"),
    _Model(702, 70, "Fabia"),
    _Model(851, 85, "GLA-Class"),
    _Model(301, 30, "500"),
    _Model(311, 31, "500"),
    _Model(51, 5, "Duster"),
    _Model(61, 6, "Duster"),
    _Model(71, 7, "Reno"),
    _Model(1011, 101, "2107"),
]
# (alias_normalized, brand_id, model_id) — shapes as on prod 2026-09-28:
# «октавия» (RU) is an alias, «октавія» (UA) is not; «500», «ваз», «рено»,
# «дастер» span several brands.
ALIASES = [
    ("шкода", 70, None),
    ("мерседес", 85, None),
    ("мерседес бенц", 85, None),
    ("фіат", 30, None),
    ("октавия", 70, 701),
    ("500", 30, 301),
    ("500", 31, 311),
    ("рено", 5, None),
    ("рено", 7, 71),
    ("дастер", 5, 51),
    ("дастер", 6, 61),
    ("ваз", 100, None),
    ("ваз", 101, None),
    ("лада", 101, None),
]
_KITS = [{"bolt_count": 5, "pcd": 112, "dia": 57.1}]


def _model_name(mid: int | None) -> str | None:
    return next((m.name for m in MODELS if m.id == mid), None)


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


class _Conn:
    """Answers the vehicle + wheel SQL from the catalogue above."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _Conn:
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
            best = [(_similarity(n, p["name"]), i, n) for i, n in BRANDS.items()]
            best = [b for b in best if b[0] > 0.3]
            return [{"id": i, "name": n} for _, i, n in sorted(best, reverse=True)[:1]]
        if "FROM vehicle_aliases va" in sql and "va.brand_id = :bid" in sql:
            hits = [
                {"model_id": mid, "name": _model_name(mid)}
                for a, b, mid in ALIASES
                if a == p["norm"] and b == p["bid"] and mid is not None
            ]
            return hits[:2]
        if "FROM vehicle_aliases va" in sql:
            return [
                {
                    "brand_id": b,
                    "brand_name": BRANDS[b],
                    "model_id": mid,
                    "model_name": _model_name(mid),
                    "source": "auto_translit",
                }
                for a, b, mid in ALIASES
                if a == p["norm"]
            ]
        if "FROM vehicle_models" in sql and "LOWER(name) = LOWER(:name)" in sql:
            ms = [
                m for m in MODELS if m.brand_id == p["bid"] and m.name.lower() == p["name"].lower()
            ]
            return [{"id": m.id, "name": m.name} for m in ms]
        if "FROM vehicle_models" in sql and "LIKE :pattern" in sql:
            prefix = re.sub(r"\\(.)", r"\1", p["pattern"][:-1])
            ms = [m for m in MODELS if m.brand_id == p["bid"] and m.name.lower().startswith(prefix)]
            return [{"id": m.id, "name": m.name} for m in ms]
        if "FROM vehicle_models" in sql and "similarity" in sql:
            scored = [
                (_similarity(m.name, p["name"]), m)
                for m in MODELS
                if m.brand_id == p["bid"] and _similarity(m.name, p["name"]) > 0.3
            ]
            scored.sort(key=lambda t: t[0], reverse=True)
            return [{"id": m.id, "name": m.name} for _, m in scored[:1]]
        if "SELECT k.id FROM vehicle_kits" in sql:
            return [{"id": 1}]
        if "FROM vehicle_disk_sizes" in sql:
            return [{"width": 6.5, "diameter": 16, "et": 46}]
        if "FROM vehicle_kits" in sql:
            return list(_KITS)
        if "FROM disk_products" in sql:
            return []
        raise AssertionError(f"unexpected SQL: {sql}")

    def looked_up(self) -> list[str]:
        """Every string the lookups were asked about (names, aliases, models)."""
        keys = ("name", "norm", "pattern")
        return [str(p[k]) for _, p in self.calls for k in keys if k in p]


class _Engine:
    def __init__(self) -> None:
        self.conn = _Conn()

    def connect(self) -> _Conn:
        return self.conn


def _client() -> tuple[StoreClient, _Engine]:
    engine = _Engine()
    return StoreClient(base_url="http://x", api_key="k", db_engine=engine), engine


def _resolve(text: str) -> tuple[dict[str, Any] | None, _Conn]:
    client, engine = _client()
    return asyncio.run(client.resolve_vehicle_text(text)), engine.conn


# ── the pure half ────────────────────────────────────────────────────────

_NOT_A_CAR = (
    "диски",
    "литі",
    "литые",
    "штамповані",
    "радіус",
    "радиус",
    "шістнадцятий",
    "шестнадцатый",
    "r16",
    "р16",
    "16",
    "на",
    "для",
    "потрібні",
    "нужны",
    "шини",
    "резину",
    "є",
    "а",
)


@pytest.mark.parametrize("word", _NOT_A_CAR)
def test_wheel_size_and_filler_words_are_not_car_words(word: str) -> None:
    words, _ = vehicle_words(f"{word} Шкоду Октавію")
    assert words == ["шкоду", "октавію"]


def test_year_is_taken_out_and_bounded() -> None:
    this_year = datetime.date.today().year
    assert vehicle_words("Октавія 2018-го року") == (["октавія"], 2018)
    assert vehicle_words(f"Октавія {this_year}") == (["октавія"], this_year)
    # outside 1980..this year: not a year (ВАЗ 2107 is a model)
    assert vehicle_words("ВАЗ 2107") == (["ваз", "2107"], None)
    assert vehicle_words("Октавія 1979")[1] is None
    assert vehicle_words(f"Октавія {this_year + 1}")[1] is None


def test_nominative_forms_ua_and_ru() -> None:
    assert "шкода" in word_forms("шкоду")
    forms = word_forms("октавію")
    assert forms[0] == "октавію"  # as heard first
    assert {"октавія", "октавия"} <= set(forms)
    assert word_forms("x5") == ["x5"]  # Latin / codes are not declined


# ── resolve_vehicle_text ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (OCTAVIA, {"brand": "Skoda", "model": "Octavia", "year": 2018}),
        ("литые диски R16 на шкоду октавию 2018 года", {"brand": "Skoda", "model": "Octavia", "year": 2018}),
        ("диски на Октавію", {"brand": "Skoda", "model": "Octavia"}),
        # «рено» and «дастер» each span two brands; together they agree on one
        ("диски на Рено Дастер 2015", {"brand": "Renault", "model": "Duster", "year": 2015}),
        ("литі диски на Фіат 500", {"brand": "Fiat", "model": "500"}),
        ("диски на ладу 2107", {"brand": "Lada", "model": "2107"}),
        # the model word before the brand: its own alias names it
        ("диски на Октавію Шкоду 2018", {"brand": "Skoda", "model": "Octavia", "year": 2018}),
        # a two-word brand: «бенц» is the brand, not the model query
        ("литі диски на Мерседес Бенц ГЛА", {"brand": "Mercedes", "model": "GLA-Class"}),
    ],
)  # fmt: skip
def test_car_read_out_of_the_utterance(text: str, expected: dict[str, Any]) -> None:
    got, _ = _resolve(text)
    assert got == expected


@pytest.mark.parametrize(
    "text",
    [
        "диски шістнадцятий радіус на 500",  # Fiat 500 or Tank 500
        "диски на ВАЗ",  # ВАЗ or Lada
        "диски на Шкоду чи на Мерседес",  # two brands named
        "потрібні литі диски шістнадцятий радіус",  # no car at all
    ],
)
def test_no_single_brand_no_car(text: str) -> None:
    got, _ = _resolve(text)
    assert got is None


def test_unknown_model_leaves_the_brand_alone() -> None:
    got, _ = _resolve("диски на Шкоду Зюзюка 2018")
    assert got == {"brand": "Skoda", "year": 2018}


def test_stop_words_never_reach_a_lookup() -> None:
    """Brand then wheel words: the words after «Шкоду» are not a model query."""
    got, conn = _resolve("Шкоду, литі диски, шістнадцятий радіус, потрібні")
    assert got == {"brand": "Skoda"}
    looked = " ".join(conn.looked_up())
    for stop in ("диск", "литі", "радіус", "шістнадцят", "потрібн"):
        assert stop not in looked, stop


def test_no_engine_no_car() -> None:
    client = StoreClient(base_url="http://x", api_key="k")
    assert asyncio.run(client.resolve_vehicle_text(OCTAVIA)) is None


# ── search_disks(vehicle_text=…) ─────────────────────────────────────────


def _kits_asked(conn: _Conn) -> list[dict[str, Any]]:
    return [p for sql, p in conn.calls if "FROM vehicle_kits k WHERE" in sql and "mid" in p]


def test_search_disks_reads_the_car_from_the_text() -> None:
    client, engine = _client()
    result = asyncio.run(client.search_disks(diameter=16, vehicle_text=OCTAVIA))
    vehicle = result["vehicle"]
    assert vehicle["found"] is True
    assert (vehicle["brand"], vehicle["model"], vehicle.get("year")) == ("Skoda", "Octavia", 2018)
    assert {p["mid"] for p in _kits_asked(engine.conn)} == {701}


def test_named_vehicle_wins_over_the_text() -> None:
    client, engine = _client()
    result = asyncio.run(
        client.search_disks(
            diameter=16, vehicle={"brand": "Skoda", "model": "Fabia"}, vehicle_text=OCTAVIA
        )
    )
    assert result["vehicle"]["model"] == "Fabia"
    assert not any(
        "vehicle_aliases" in sql and p.get("norm") == "октавия" for sql, p in engine.conn.calls
    )


def test_text_without_a_car_is_no_vehicle() -> None:
    client, _ = _client()
    result = asyncio.run(client.search_disks(diameter=16, vehicle_text="литі диски 16 радіус"))
    assert "vehicle" not in result


# ── the substitution passes the utterance ────────────────────────────────

_TOOLS = [{"name": "search_disks"}, {"name": "search_tires"}, {"name": "get_vehicle_tire_sizes"}]


def _substitute(text: str, tool: str, args: dict[str, Any], execute: Any) -> str:
    redirect = DiskToolRedirect(sales_enabled=True, tools=_TOOLS)
    sub = redirect.check(tool, args, [{"role": "user", "content": text}])
    assert sub is not None
    return asyncio.run(run_disk_substitution(sub, execute, timeout=5, sales_enabled=True))


@pytest.mark.parametrize(
    ("tool", "args"),
    [("search_tires", {"diameter": 15}), ("search_knowledge_base", {"query": "диски"})],
)
def test_substitution_without_a_car_passes_the_utterance(tool: str, args: dict[str, Any]) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
        ran.append((name, dict(call_args)))
        return {"total": 0, "items": []}

    _substitute(OCTAVIA, tool, args, execute)
    assert ran == [("search_disks", {"diameter": 16, "vehicle_text": OCTAVIA})]


def test_substitution_with_the_car_in_the_call_passes_no_text() -> None:
    ran: list[dict[str, Any]] = []

    async def execute(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
        ran.append(dict(call_args))
        return {"total": 0, "items": []}

    car = {"brand": "Skoda", "model": "Octavia", "year": 2018}
    _substitute(OCTAVIA, "get_vehicle_tire_sizes", car, execute)
    assert ran == [{"diameter": 16, "vehicle": car}]


def test_substitution_end_to_end_reaches_the_car() -> None:
    """Substitution → the real `search_disks` → the car from the caller's words."""
    client, _ = _client()
    raw: list[dict[str, Any]] = []

    async def execute(name: str, call_args: dict[str, Any]) -> dict[str, Any]:
        assert name == "search_disks"
        result = await client.search_disks(**call_args)
        raw.append(result)
        return result

    _substitute(OCTAVIA, "search_tires", {"diameter": 16}, execute)
    [result] = raw
    assert result["vehicle"]["found"] is True
    assert (result["vehicle"]["brand"], result["vehicle"]["model"]) == ("Skoda", "Octavia")
