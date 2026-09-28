"""check_disk_fit — tshina ``DiskFitmentChecker`` rules, default-deny.

Wave 5-N (orders-consult-networks 2026-09-28). Rules: FINDINGS I §7.
Invariants, not expected numbers: a verdict is the worst of its checks; a
missing car fact never yields ``fits``; PCD is exact.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from src.agent import disk_fitment as fit
from src.agent.disk_fitment import (
    CarFitData,
    CarWheelSize,
    DiskSpec,
    check_disk_fit,
    disk_bolt_patterns,
    parse_pcd,
)

D = Decimal


def _car(
    pcds: tuple[tuple[int, str], ...] = ((5, "114.3"),),
    dias: tuple[str, ...] = ("60.1",),
    sizes: tuple[tuple[str, int, str | None], ...] = (("7", 17, "45"),),
) -> CarFitData:
    return CarFitData(
        pcds=tuple((b, D(p)) for b, p in pcds),
        dias=tuple(D(d) for d in dias),
        sizes=tuple(CarWheelSize(D(w), dm, D(e) if e is not None else None) for w, dm, e in sizes),
    )


def _disk(
    bolts: int = 5,
    pcd: str = "114.3",
    pcd_alt: str | None = None,
    dia: str = "60.1",
    width: str = "7",
    diameter: int = 17,
    et: str = "45",
) -> DiskSpec:
    return DiskSpec(
        diameter=diameter,
        width=D(width),
        bolt_count=bolts,
        pcd=D(pcd),
        pcd_alt=D(pcd_alt) if pcd_alt else None,
        et=D(et),
        dia=D(dia),
    )


def _reason(verdict: fit.FitVerdict, param: str) -> fit.FitReason:
    [r] = [r for r in verdict.reasons if r.param == param]
    return r


class TestBaseline:
    def test_exact_factory_wheel_fits(self) -> None:
        v = check_disk_fit(_disk(), _car())
        assert v.status == fit.FITS
        assert {r.param for r in v.reasons} == {"pcd", "dia", "size", "et"}


class TestPcd:
    def test_numeric_normalisation_car_114_30_equals_disk_114_3(self) -> None:
        car = CarFitData.from_rows(
            [{"bolt_count": 5, "pcd": D("114.30"), "dia": D("60.10")}],
            [{"width": D("7.00"), "diameter": D("17.0"), "et": D("45.0")}],
        )
        assert check_disk_fit(_disk(), car).status == fit.FITS

    @pytest.mark.parametrize(
        ("disk_bolts", "disk_pcd", "car_bolts", "car_pcd"),
        [
            (5, "115", 5, "114.3"),  # real, distinct patterns 0.7 mm apart
            (5, "114.3", 5, "115"),
            (4, "98", 4, "100"),
            (5, "112", 5, "112.5"),
            (4, "100", 5, "100"),  # bolt count differs
        ],
    )
    def test_near_miss_pcd_is_not_fit(
        self, disk_bolts: int, disk_pcd: str, car_bolts: int, car_pcd: str
    ) -> None:
        v = check_disk_fit(
            _disk(bolts=disk_bolts, pcd=disk_pcd), _car(pcds=((car_bolts, car_pcd),))
        )
        assert _reason(v, "pcd").status == fit.NOT_FIT
        assert v.status == fit.NOT_FIT

    @pytest.mark.parametrize("car_pcd", ["100", "112"])
    def test_multi_pcd_wheel_fits_either_pattern(self, car_pcd: str) -> None:
        v = check_disk_fit(_disk(pcd="100", pcd_alt="112"), _car(pcds=((5, car_pcd),)))
        assert _reason(v, "pcd").status == fit.FITS

    def test_multi_pcd_wheel_neither_pattern_is_not_fit(self) -> None:
        v = check_disk_fit(_disk(pcd="100", pcd_alt="112"), _car(pcds=((5, "114.3"),)))
        assert _reason(v, "pcd").status == fit.NOT_FIT

    @pytest.mark.parametrize("car_pcd", ["100", "114.3"])
    def test_double_drilled_8_hole_is_two_4_bolt_patterns(self, car_pcd: str) -> None:
        disk = _disk(bolts=8, pcd="100", pcd_alt="114.3")
        assert _reason(check_disk_fit(disk, _car(pcds=((4, car_pcd),))), "pcd").status == fit.FITS
        # …and never an 8- or 5-bolt hub
        assert (
            _reason(check_disk_fit(disk, _car(pcds=((5, car_pcd),))), "pcd").status == fit.NOT_FIT
        )

    def test_patterns(self) -> None:
        assert disk_bolt_patterns(_disk(bolts=8, pcd="100", pcd_alt="114.3")) == (
            (4, D("100")),
            (4, D("114.3")),
        )
        assert disk_bolt_patterns(_disk(bolts=8, pcd="100")) == ((4, D("100")),)
        # truck pattern stays whole
        assert disk_bolt_patterns(_disk(bolts=10, pcd="335")) == ((10, D("335")),)
        assert disk_bolt_patterns(_disk(bolts=5, pcd="100", pcd_alt="112")) == (
            (5, D("100")),
            (5, D("112")),
        )

    @pytest.mark.parametrize("text", ["5x114.3", "5х114,3", "5 × 114.30", "5*114.3", "5/114.3"])
    def test_parse_pcd_forms(self, text: str) -> None:
        assert parse_pcd(text) == (5, D("114.3"))

    @pytest.mark.parametrize("text", ["", "114.3", "5x", "abc", "0x100", "100x100", None, 5])
    def test_parse_pcd_rejects(self, text: object) -> None:
        assert parse_pcd(text) is None


class TestDia:
    def test_equal_within_tolerance_fits(self) -> None:
        v = check_disk_fit(_disk(dia="60.14"), _car(dias=("60.1",)))
        assert _reason(v, "dia").status == fit.FITS

    def test_larger_bore_needs_rings(self) -> None:
        v = check_disk_fit(_disk(dia="67.1"), _car(dias=("60.1",)))
        assert _reason(v, "dia").status == fit.FITS_WITH_RINGS
        assert v.status == fit.FITS_WITH_RINGS

    def test_smaller_bore_does_not_fit(self) -> None:
        v = check_disk_fit(_disk(dia="57.1"), _car(dias=("60.1",)))
        assert _reason(v, "dia").status == fit.NOT_FIT
        assert v.status == fit.NOT_FIT


class TestEt:
    @pytest.mark.parametrize(
        ("disk_et", "expected"),
        [
            ("45", fit.FITS),
            ("40", fit.FITS),  # 5 mm
            ("50", fit.FITS),
            ("39", fit.CHECK_IN_CENTER),  # 6 mm
            ("37", fit.CHECK_IN_CENTER),  # 8 mm
            ("53", fit.CHECK_IN_CENTER),  # 8 mm
            ("35", fit.CHECK_IN_CENTER),  # 10 mm
            ("34", fit.NOT_RECOMMENDED),  # 11 mm
            ("20", fit.NOT_RECOMMENDED),
        ],
    )
    def test_thresholds(self, disk_et: str, expected: str) -> None:
        v = check_disk_fit(_disk(et=disk_et), _car())
        assert _reason(v, "et").status == expected
        assert v.status == expected

    def test_half_millimetre_rounds_up_like_tshina(self) -> None:
        # PHP round(5.5) = 6 → check, not fits.
        v = check_disk_fit(_disk(et="39.5"), _car())
        assert _reason(v, "et").status == fit.CHECK_IN_CENTER

    def test_nearest_of_same_size_wins_over_other_sizes(self) -> None:
        car = _car(sizes=(("7", 17, "45"), ("8", 18, "35")))
        v = check_disk_fit(_disk(et="35"), car)
        # 7J R17 factory ET45 → 10 mm, although 8J R18 has ET35
        assert _reason(v, "et").diff == 10
        assert _reason(v, "et").status == fit.CHECK_IN_CENTER

    def test_nearest_among_several_factory_ets(self) -> None:
        car = _car(sizes=(("7", 17, "45"), ("7", 17, "39")))
        v = check_disk_fit(_disk(et="37"), car)
        assert _reason(v, "et").diff == 2
        assert _reason(v, "et").status == fit.FITS

    def test_falls_back_to_same_diameter_then_any(self) -> None:
        car = _car(sizes=(("6.5", 17, "45"), ("8", 18, "35")))
        v = check_disk_fit(_disk(width="7", et="44"), car)
        assert _reason(v, "size").status == fit.CHECK_IN_CENTER
        assert _reason(v, "et").car == "45"
        car2 = _car(sizes=(("8", 18, "38"),))
        assert _reason(check_disk_fit(_disk(et="40"), car2), "et").car == "38"


class TestSize:
    def test_width_not_listed_is_check_in_center(self) -> None:
        v = check_disk_fit(_disk(width="8.5"), _car())
        assert _reason(v, "size").status == fit.CHECK_IN_CENTER
        assert v.status == fit.CHECK_IN_CENTER

    def test_diameter_not_listed_is_check_in_center(self) -> None:
        v = check_disk_fit(_disk(diameter=19), _car())
        assert _reason(v, "size").status == fit.CHECK_IN_CENTER


class TestDefaultDeny:
    """A missing car fact never reads as "fits"."""

    def test_car_without_pcd_cannot_confirm(self) -> None:
        v = check_disk_fit(_disk(), _car(pcds=()))
        assert _reason(v, "pcd").status == fit.CANNOT_CONFIRM
        assert v.status == fit.CANNOT_CONFIRM
        assert v.status != fit.FITS

    def test_car_without_dia_cannot_confirm(self) -> None:
        assert check_disk_fit(_disk(), _car(dias=())).status == fit.CANNOT_CONFIRM

    def test_car_without_sizes_is_not_fits(self) -> None:
        v = check_disk_fit(_disk(), _car(sizes=()))
        assert v.status not in (fit.FITS, fit.FITS_WITH_RINGS)

    def test_car_sizes_without_et_cannot_confirm(self) -> None:
        v = check_disk_fit(_disk(), _car(sizes=(("7", 17, None),)))
        assert _reason(v, "et").status == fit.CANNOT_CONFIRM

    def test_disk_without_pcd_cannot_confirm(self) -> None:
        disk = DiskSpec(diameter=17, width=D("7"), et=D("45"), dia=D("60.1"))
        assert check_disk_fit(disk, _car()).status == fit.CANNOT_CONFIRM

    def test_empty_car_never_fits(self) -> None:
        assert check_disk_fit(_disk(), CarFitData()).status != fit.FITS

    def test_junk_kit_values_do_not_count_as_pcd(self) -> None:
        car = CarFitData.from_rows(
            [
                {"bolt_count": 0, "pcd": D("100"), "dia": None},
                {"bolt_count": 100, "pcd": D("100"), "dia": D("0")},
                {"bolt_count": None, "pcd": None, "dia": None},
            ]
        )
        assert car.pcds == ()
        assert car.dias == ()

    def test_unknown_status_ranks_worst(self) -> None:
        assert fit.worst([fit.FITS, "something_new"]) == fit.NOT_FIT


class TestAmbiguousCar:
    def test_two_pcds(self) -> None:
        car = _car(pcds=((5, "114.3"), (5, "112")))
        v = check_disk_fit(_disk(), car)
        assert v.status == fit.AMBIGUOUS_CAR
        assert "5x112" in (v.reasons[0].car or "")

    def test_two_hub_bores(self) -> None:
        assert check_disk_fit(_disk(), _car(dias=("60.1", "67.1"))).status == fit.AMBIGUOUS_CAR

    def test_same_pcd_written_twice_is_not_ambiguous(self) -> None:
        car = CarFitData.from_rows(
            [
                {"bolt_count": 5, "pcd": D("114.30"), "dia": D("60.10")},
                {"bolt_count": 5, "pcd": D("114.3"), "dia": D("60.1")},
            ]
        )
        assert len(car.pcds) == 1
        assert len(car.dias) == 1


class TestVerdictIsWorstOfChecks:
    """Corpus over a grid: overall = worst check; fits ⇒ every check fits."""

    GRID: ClassVar[list[tuple[Any, ...]]] = list(
        itertools.product(
            [("5", "114.3", None), ("5", "112", None), ("5", "100", "114.3")],  # disk pcd
            ["60.1", "67.1", "57.1"],  # disk dia
            ["45", "38", "30"],  # disk et
            ["7", "8"],  # width
            [((5, "114.3"),), ()],  # car pcds
            [("60.1",), ()],  # car dias
        )
    )

    @pytest.mark.parametrize("case", GRID)
    def test_invariants(self, case: tuple[Any, ...]) -> None:
        (bolts, pcd, alt), dia, et, width, car_pcds, car_dias = case
        v = check_disk_fit(
            _disk(bolts=int(bolts), pcd=pcd, pcd_alt=alt, dia=dia, et=et, width=width),
            _car(pcds=car_pcds, dias=car_dias),
        )
        assert v.status == fit.worst(r.status for r in v.reasons)
        if v.status == fit.FITS:
            assert all(r.status == fit.FITS for r in v.reasons)
        if not car_pcds or not car_dias:
            assert v.status not in (fit.FITS, fit.FITS_WITH_RINGS)


def test_verdict_dict_has_ukrainian_text() -> None:
    d = check_disk_fit(_disk(dia="67.1"), _car()).as_dict()
    assert d["status"] == fit.FITS_WITH_RINGS
    assert d["text"] == fit.VERDICT_TEXT_UK[fit.FITS_WITH_RINGS]
    assert set(fit.VERDICT_TEXT_UK) >= set(fit.SEVERITY) | {fit.AMBIGUOUS_CAR}


def test_no_spacer_or_redrilling_advice_in_texts() -> None:
    for text in fit.VERDICT_TEXT_UK.values():
        assert "проставк" not in text.lower()
        assert "свердл" not in text.lower()
