"""Tests for the multi-car booking detector (owner request 2026-10-27).

R04: up to three cars per call, a fourth and more is a fleet → operator.
R05: only a caller who says it himself — no false «дві машини» from tyre
counts, friend's cars or ordinals of days.
"""

from __future__ import annotations

import pytest

from src.agent.multi_car_detect import MAX_CARS_PER_CALL, MultiCarIntent, detect_multi_car


def test_cap_is_three() -> None:
    assert MAX_CARS_PER_CALL == 3


# (text, count, more, over_cap)
_UA_POSITIVES = [
    ("мені треба записати на шиномонтаж дві машини, бажано на завтра", 2, False, False),
    ("можна записати три машинки на шиномонтаж?", 3, False, False),
    ("двома машинками можна, да?", 2, False, False),
    ("записатися на наступний день двома машинками", 2, False, False),
    ("у мене два авто", 2, False, False),
    ("треба переобути двох автомобілів", 2, False, False),
    ("хочу записати дві різні машини", 2, False, False),
    ("запишіть обидві машини на суботу", 2, False, False),
    ("трьох машин на одну годину", 3, False, False),
    ("у нас троє машин", 3, False, False),
    ("2 машини на перевзування", 2, False, False),
    ("3 авто на завтра", 3, False, False),
    ("ще одну машину запишіть", None, True, False),
    ("і ще одну, будь ласка", None, True, False),
    ("а можна ще одну запишіть", None, True, False),
    ("і другу теж", None, True, False),
    ("друге авто теж треба", None, True, False),
    ("запишіть другу машину на той самий час", None, True, False),
    ("ще дві машини треба", 2, True, False),
    ("ще один автомобіль", None, True, False),
    ("кілька машин хочу записати", None, False, False),
    ("треба записати дві автівки", 2, False, False),
]

_RU_POSITIVES = [
    ("мне надо записать две машины одновременно", 2, False, False),
    ("одновременно две машины на 11 часов", 2, False, False),
    ("записать двух автомобилей", 2, False, False),
    ("пару машин на переобувку", 2, False, False),
    ("у меня две легковые машины", 2, False, False),
    ("три машины на субботу", 3, False, False),
    ("обе машины запишите", 2, False, False),
    ("ещё одну машину", None, True, False),
    ("а ещё одну, пожалуйста", None, True, False),
    ("и вторую тоже", None, True, False),
    ("вторую машину тоже запишите", None, True, False),
    ("еще один автомобиль надо", None, True, False),
    ("несколько машин надо переобуть", None, False, False),
    ("двумя машинами приедем", 2, False, False),
    ("две автомашины на шиномонтаж", 2, False, False),
]

_OVER_CAP = [
    ("чотири машини треба записати", 4, False, True),
    ("п'ять авто на перевзування", 5, False, True),
    ("п’ять машин", 5, False, True),
    ("четыре машины", 4, False, True),
    ("у нас автопарк, треба переобути", None, False, True),
    ("10 машин", 10, False, True),
    ("шесть автомобилей", 6, False, True),
]

_POSITIVES = _UA_POSITIVES + _RU_POSITIVES + _OVER_CAP


@pytest.mark.parametrize(("text", "count", "more", "over_cap"), _POSITIVES)
def test_positive(text: str, count: int | None, more: bool, over_cap: bool) -> None:
    assert detect_multi_car(text) == MultiCarIntent(count=count, more=more, over_cap=over_cap)


_NEGATIVES = [
    "два колеса",
    "дві шини",
    "4 колеса",
    "4 колеса машини",
    "поміняти два колеса машини",
    "дві шини машини",
    "две шины на машину",
    "машина друга",
    "у друга машина така сама",
    "для друга авто записати",
    "другий раз приїду",
    "машина на зберіганні",
    "машина на хранении",
    "друга половина дня",
    "другого числа",
    "на два дні машину залишити",
    "на дві години машину",
    "ще одне питання",
    "ще одну шину",
    "ещё один вопрос",
    "одна машина",
    "1 машина",
    "запишіть мою машину",
    "R16, чотири шини",
    "",
    "автомат",
    "ще раз",
]


@pytest.mark.parametrize("text", _NEGATIVES)
def test_negative(text: str) -> None:
    assert detect_multi_car(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "у мене дві машини, але запис на одну",
        "у меня две машины, но только одну запишите",
        "дві машини, тільки одну поки що",
        "у нас автопарк, але тільки на одну",
        "четыре машины, но запишите одну",
    ],
)
def test_explicit_limit_is_one(text: str) -> None:
    assert detect_multi_car(text) == MultiCarIntent(count=1, more=False, over_cap=False)


def test_more_wins_over_limit_word() -> None:
    # «но» before «ещё одну» is not a limit.
    assert detect_multi_car("но ещё одну машину") == MultiCarIntent(
        count=None, more=True, over_cap=False
    )


def test_corpus_sizes() -> None:
    assert len(_POSITIVES) >= 30
    assert len(_NEGATIVES) >= 20
