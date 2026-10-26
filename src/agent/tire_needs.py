"""`tire_needs` — a need the caller named, scored on a catalogue row.

Port of tshina `TireConsultant/Services/Recommendation/NeedsMatcher.php`
(``013e0cd24``): «тихі», «щоб на мокрому тримали», «економні по пальному»,
«для траси» order the offer — the need first, the network's
``brand_priority`` only between tyres that fit it equally (owner's decision
in tshina: the need beats the promotion).

Tiers per need, not a continuous score: 70 against 71 dB must not override
the network's promotion — only a noticeable difference does.

- ``quiet``    — EU label noise: ≤ 69 dB → 2, 70–71 → 1, ≥ 72 → 0;
- ``wet_grip`` — EU label wet grip: A → 2, B → 1, C…E → 0;
- ``fuel``     — EU label fuel class: A/B → 2, C → 1, D/E → 0;
- ``highway``  — speed index: V and above (V, W, Y, Z/ZR) → 2, H → 1, lower → 0.

No data (no EU label row, no speed index) → 0 here, where tshina gives 1:
a tyre that cannot show the need is the bottom step, never dropped — the
ranking fills the offer from the lower steps when the upper ones run short.
The stud type is a search filter (``studded``), not a need.

Pure: no I/O. The parser of the caller's words is
`src.agent.parsers.tire_query.parse_needs`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["NEED_KEYS", "Need", "need_score", "need_tier", "row_speed_index"]

Need = Literal["quiet", "wet_grip", "fuel", "highway"]

#: Every need the ranking can score, in the order the progress block lists them.
NEED_KEYS: tuple[Need, ...] = ("quiet", "wet_grip", "fuel", "highway")

#: Speed indices of 240 km/h and above (tshina ``FAST_SPEED``).
_FAST_SPEED = frozenset({"V", "W", "Y", "Z", "ZR"})

#: «225/45 R17 94W», «… 94(Y)» — the speed index after the load index at the
#: end of a size string; «205/55 R16C» (no load index) carries none.
_SIZE_SPEED_RE = re.compile(r"\s\d{2,3}(?:/\d{2,3})?\s*\(?([A-Za-z]{1,2})\)?\s*$")


def _grade(value: Any) -> str | None:
    v = str(value or "").strip().upper()
    return v if re.fullmatch(r"[A-G]", v) else None


def _noise_tier(row: Mapping[str, Any]) -> int:
    noise = row.get("eu_noise_db")
    try:
        db = int(noise) if noise is not None else None
    except (TypeError, ValueError):
        db = None
    if db is None or not 40 <= db <= 100:
        return 0
    if db <= 69:
        return 2
    if db <= 71:
        return 1
    return 0


def _grade_tier(grade: str | None, good: frozenset[str], fair: frozenset[str]) -> int:
    if grade in good:
        return 2
    if grade in fair:
        return 1
    return 0


def row_speed_index(row: Mapping[str, Any]) -> str | None:
    """Speed index of a row: ``speed_index`` (tshina attribute), else the size tail."""
    raw = str(row.get("speed_index") or "").strip().upper().strip("()")
    if not raw:
        m = _SIZE_SPEED_RE.search(str(row.get("size") or ""))
        raw = m.group(1).upper() if m else ""
    return raw or None


def _speed_tier(row: Mapping[str, Any]) -> int:
    speed = row_speed_index(row)
    if speed in _FAST_SPEED:
        return 2
    if speed == "H":
        return 1
    return 0


def need_tier(row: Mapping[str, Any], need: str) -> int:
    """0–2: how well one catalogue row (a `_query_tire_rows` row) meets ``need``."""
    if need == "quiet":
        return _noise_tier(row)
    if need == "wet_grip":
        return _grade_tier(_grade(row.get("eu_wet")), frozenset("A"), frozenset("B"))
    if need == "fuel":
        return _grade_tier(_grade(row.get("eu_fuel")), frozenset("AB"), frozenset("C"))
    if need == "highway":
        return _speed_tier(row)
    return 0


def need_score(row: Mapping[str, Any], needs: Iterable[str]) -> int:
    """Sum of `need_tier` over the named needs — the step of the row in the offer."""
    return sum(need_tier(row, n) for n in needs)
