"""Does this wheel fit this car? — decided in code, never by the LLM.

Port of tshina ``DiskFitmentChecker::check`` (Modules/TireConsultant/Services/
Vehicle/DiskFitmentChecker.php, owner verdicts 2026-09-25) with the gaps
closed default-deny:

- PCD — exact match after normalisation (``114.30`` = ``114.3``), bolts × PCD
  (:74-78, :314-322). A multi-PCD wheel (``5/100-112`` → ``pcd_alt``) fits when
  either pattern matches; an 8/10-hole double-drilled wheel (``8/100-114.3``)
  is two 4-bolt patterns. tshina did not support either.
- DIA (:81-88) — |Δ| < 0.05 fits; wheel bore larger than the hub — fits with
  centring rings; smaller — does not fit.
- Size (:90-98) — width × diameter not among the car's sizes → check in the
  tyre centre.
- ET (:100-117) — against the nearest factory ET (same size → same diameter →
  any size): ≤5 mm fits, 6–10 mm — check clearances at fitting, >10 mm — not
  recommended.
- Car with >1 PCD or hub bore (:66-68) → ``ambiguous_car``: ask the year or
  the modification.
- The overall verdict is the worst check (:43-49, :119-124).

Where tshina silently skipped a check (car without PCD, hub bore or ET) this
module reports ``cannot_confirm`` instead: missing data never reads as
"fits". Spacers and re-drilling are never offered (tshina owner rule) — a PCD
mismatch is ``not_fit``, full stop.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# ── Verdicts ──────────────────────────────────────────────────────────────

FITS = "fits"
FITS_WITH_RINGS = "fits_with_rings"
CHECK_IN_CENTER = "check_in_center"
CANNOT_CONFIRM = "cannot_confirm"
NOT_RECOMMENDED = "not_recommended"
NOT_FIT = "not_fit"
AMBIGUOUS_CAR = "ambiguous_car"

#: Severity for "the worst check wins". An unknown status ranks as the worst
#: (default-deny).
SEVERITY: dict[str, int] = {
    FITS: 0,
    FITS_WITH_RINGS: 1,
    CHECK_IN_CENTER: 2,
    CANNOT_CONFIRM: 3,
    NOT_RECOMMENDED: 4,
    NOT_FIT: 5,
}
_WORST = max(SEVERITY.values()) + 1

#: Verdicts a wheel may be offered with (anything else stays out of the offer).
OFFERABLE: frozenset[str] = frozenset({FITS, FITS_WITH_RINGS, CHECK_IN_CENTER, CANNOT_CONFIRM})

#: DIA equality tolerance (tshina :84).
DIA_TOLERANCE = Decimal("0.05")
#: ET thresholds, mm (tshina :114).
ET_FITS_MAX = 5
ET_CHECK_MAX = 10

#: Plausible bolt counts of a car hub; 0 / 100 in ``vehicle_kits`` are junk.
_CAR_BOLTS = range(3, 11)
#: 8/10-hole wheels below this PCD are double-drilled passenger wheels
#: (``8/100-114.3``); truck patterns (8×165.1, 10×335) are above it.
_DOUBLE_DRILLED_PCD_MAX = Decimal("150")

#: Ukrainian one-liners per verdict — the LLM repeats, it does not compose.
VERDICT_TEXT_UK: dict[str, str] = {
    FITS: "підходить",
    FITS_WITH_RINGS: "підходить з центрувальними кільцями",
    CHECK_IN_CENTER: "посадку треба перевірити на шиномонтажі",
    CANNOT_CONFIRM: "не можу підтвердити сумісність — немає даних по авто",
    NOT_RECOMMENDED: "не рекомендуємо",
    NOT_FIT: "не підходить",
    AMBIGUOUS_CAR: "уточніть рік або модифікацію авто",
}


# ── Data ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiskSpec:
    """A wheel as ``disk_products`` holds it (``None`` — unknown)."""

    diameter: int | None = None
    width: Decimal | None = None
    bolt_count: int | None = None
    pcd: Decimal | None = None
    pcd_alt: Decimal | None = None
    et: Decimal | None = None
    dia: Decimal | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> DiskSpec:
        return cls(
            diameter=to_int(row.get("diameter")),
            width=to_decimal(row.get("width_j", row.get("width"))),
            bolt_count=to_int(row.get("bolt_count")),
            pcd=to_decimal(row.get("pcd")),
            pcd_alt=to_decimal(row.get("pcd_alt")),
            et=to_decimal(row.get("et")),
            dia=to_decimal(row.get("dia")),
        )


@dataclass(frozen=True)
class CarWheelSize:
    """One factory / acceptable wheel size of a car (``vehicle_disk_sizes``)."""

    width: Decimal | None
    diameter: int | None
    et: Decimal | None


@dataclass(frozen=True)
class CarFitData:
    """What the car accepts: distinct PCD patterns, hub bores and wheel sizes."""

    pcds: tuple[tuple[int, Decimal], ...] = ()
    dias: tuple[Decimal, ...] = ()
    sizes: tuple[CarWheelSize, ...] = ()

    @classmethod
    def from_rows(
        cls, kits: Iterable[Mapping[str, Any]], sizes: Iterable[Mapping[str, Any]] = ()
    ) -> CarFitData:
        """Union over ``vehicle_kits`` rows (bolt_count, pcd, dia) and
        ``vehicle_disk_sizes`` rows (width, diameter, et). Junk values drop."""
        pcds: list[tuple[int, Decimal]] = []
        dias: list[Decimal] = []
        for k in kits:
            bolts = to_int(k.get("bolt_count"))
            pcd = to_decimal(k.get("pcd"))
            if bolts in _CAR_BOLTS and pcd is not None and pcd > 0:
                pattern = (bolts, pcd)
                if pattern not in pcds:
                    pcds.append(pattern)
            dia = to_decimal(k.get("dia"))
            if dia is not None and dia > 0 and dia not in dias:
                dias.append(dia)
        car_sizes: list[CarWheelSize] = []
        for s in sizes:
            size = CarWheelSize(
                width=to_decimal(s.get("width")),
                diameter=to_int(s.get("diameter")),
                et=to_decimal(s.get("et")),
            )
            if size not in car_sizes:
                car_sizes.append(size)
        return cls(pcds=tuple(pcds), dias=tuple(dias), sizes=tuple(car_sizes))


@dataclass(frozen=True)
class FitReason:
    """One check: ``param`` in pcd / dia / size / et / car."""

    param: str
    status: str
    disk: str | None = None
    car: str | None = None
    diff: int | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"param": self.param, "status": self.status}
        if self.disk is not None:
            d["disk"] = self.disk
        if self.car is not None:
            d["car"] = self.car
        if self.diff is not None:
            d["diff_mm"] = self.diff
        return d


@dataclass(frozen=True)
class FitVerdict:
    status: str
    reasons: tuple[FitReason, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": VERDICT_TEXT_UK.get(self.status, VERDICT_TEXT_UK[CANNOT_CONFIRM]),
            "reasons": [r.as_dict() for r in self.reasons],
        }


# ── Normalisation helpers ─────────────────────────────────────────────────


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def to_int(value: Any) -> int | None:
    d = to_decimal(value)
    if d is None or d != d.to_integral_value():
        return None
    return int(d)


def num_str(value: Decimal | int | None) -> str | None:
    """``114.30`` → ``114.3``, ``7.0`` → ``7``."""
    if value is None:
        return None
    d = Decimal(value)
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def pcd_str(pattern: tuple[int, Decimal]) -> str:
    return f"{pattern[0]}x{num_str(pattern[1])}"


_PCD_RE = re.compile(r"^\s*(\d{1,2})\s*[xх×*/]\s*(\d{2,3}(?:[.,]\d{1,2})?)\s*$", re.IGNORECASE)


def parse_pcd(text: Any) -> tuple[int, Decimal] | None:
    """``5x114.3`` / ``5х114,3`` / ``5*114.30`` / ``5/114.3`` → ``(5, 114.3)``."""
    if not isinstance(text, str):
        return None
    m = _PCD_RE.match(text)
    if m is None:
        return None
    pcd = to_decimal(m.group(2))
    bolts = int(m.group(1))
    if pcd is None or pcd <= 0 or bolts not in _CAR_BOLTS:
        return None
    return bolts, pcd


def disk_bolt_patterns(disk: DiskSpec) -> tuple[tuple[int, Decimal], ...]:
    """Every bolt pattern the wheel mounts on.

    ``5/100-112`` → 5x100, 5x112; ``8/100-114.3`` (double-drilled) → 4x100,
    4x114.3; ``8/100`` → 4x100 only (the second pattern is unknown).
    """
    if disk.bolt_count is None or disk.pcd is None or disk.bolt_count <= 0:
        return ()
    bolts = disk.bolt_count
    double = (
        bolts >= 8
        and bolts % 2 == 0
        and (disk.pcd_alt is not None or disk.pcd < _DOUBLE_DRILLED_PCD_MAX)
    )
    if double:
        bolts //= 2
    patterns = [(bolts, disk.pcd)]
    if disk.pcd_alt is not None and disk.pcd_alt > 0:
        patterns.append((bolts, disk.pcd_alt))
    return tuple(patterns)


def _same_pattern(a: tuple[int, Decimal], b: tuple[int, Decimal]) -> bool:
    # Decimal equality is numeric: 114.30 == 114.3, 114.3 != 114.31.
    return a[0] == b[0] and a[1] == b[1]


def _round_mm(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def worst(statuses: Iterable[str]) -> str:
    result = FITS
    for s in statuses:
        if SEVERITY.get(s, _WORST) > SEVERITY.get(result, _WORST):
            result = s if s in SEVERITY else NOT_FIT
    return result


# ── The check ─────────────────────────────────────────────────────────────


def check_disk_fit(disk: DiskSpec, car: CarFitData) -> FitVerdict:
    """Verdict for ``disk`` on ``car``. Never ``fits`` on missing data."""
    if len(car.pcds) > 1 or len(car.dias) > 1:
        variants = [pcd_str(p) for p in car.pcds] + [f"DIA {num_str(d)}" for d in car.dias]
        return FitVerdict(
            AMBIGUOUS_CAR, (FitReason("car", AMBIGUOUS_CAR, car=", ".join(variants)),)
        )

    reasons: list[FitReason] = []

    # PCD — exact only; no spacers / re-drilling
    patterns = disk_bolt_patterns(disk)
    disk_pcd = "/".join(pcd_str(p) for p in patterns) or None
    if not car.pcds:
        reasons.append(FitReason("pcd", CANNOT_CONFIRM, disk=disk_pcd))
    elif not patterns:
        reasons.append(FitReason("pcd", CANNOT_CONFIRM, car=pcd_str(car.pcds[0])))
    else:
        car_pcd = car.pcds[0]
        ok = any(_same_pattern(p, car_pcd) for p in patterns)
        reasons.append(
            FitReason("pcd", FITS if ok else NOT_FIT, disk=disk_pcd, car=pcd_str(car_pcd))
        )

    # DIA — hub bore
    car_dia = car.dias[0] if car.dias else None
    if car_dia is None or disk.dia is None:
        reasons.append(
            FitReason("dia", CANNOT_CONFIRM, disk=num_str(disk.dia), car=num_str(car_dia))
        )
    else:
        if abs(disk.dia - car_dia) < DIA_TOLERANCE:
            status = FITS
        elif disk.dia > car_dia:
            status = FITS_WITH_RINGS
        else:
            status = NOT_FIT
        reasons.append(FitReason("dia", status, disk=num_str(disk.dia), car=num_str(car_dia)))

    # Size — width × diameter among the car's sizes
    same_diameter = [
        s for s in car.sizes if disk.diameter is not None and s.diameter == disk.diameter
    ]
    same_size = [s for s in same_diameter if disk.width is not None and s.width == disk.width]
    disk_size = (
        (f"{num_str(disk.width)}J " if disk.width is not None else "") + f"R{disk.diameter}"
        if disk.diameter is not None
        else None
    )
    listed = sorted(
        {f"{num_str(s.width)}J R{s.diameter}" for s in car.sizes if s.width and s.diameter}
    )
    if not car.sizes or disk.diameter is None or disk.width is None:
        reasons.append(FitReason("size", CHECK_IN_CENTER, disk=disk_size, car=None))
    else:
        reasons.append(
            FitReason(
                "size",
                FITS if same_size else CHECK_IN_CENTER,
                disk=disk_size,
                car=", ".join(listed) or None,
            )
        )

    # ET — nearest factory ET: same size → same diameter → any size
    best: tuple[int, Decimal] | None = None
    if disk.et is not None:
        for pool in (same_size, same_diameter, list(car.sizes)):
            ets = [s.et for s in pool if s.et is not None]
            if ets:
                for et in ets:
                    diff = _round_mm(abs(disk.et - et))
                    if best is None or diff < best[0]:
                        best = (diff, et)
                break
    if best is None:
        reasons.append(FitReason("et", CANNOT_CONFIRM, disk=num_str(disk.et)))
    else:
        diff, car_et = best
        if diff <= ET_FITS_MAX:
            status = FITS
        elif diff <= ET_CHECK_MAX:
            status = CHECK_IN_CENTER
        else:
            status = NOT_RECOMMENDED
        reasons.append(
            FitReason("et", status, disk=num_str(disk.et), car=num_str(car_et), diff=diff)
        )

    return FitVerdict(worst(r.status for r in reasons), tuple(reasons))
