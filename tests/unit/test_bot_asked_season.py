"""A season word in the bot's reply is a season question only in a question.

Wave 3-G finding (2026-09-28): the check matched «зимові» anywhere in the
bot's reply, a list of offered tyres included, so «бренд будь-який» on the
next turn became season «any» and overwrote «зимові». Since 3-G the code
searches on the parsed request by itself.
"""

from __future__ import annotations

import pytest

from src.core.pipeline import _bot_asked_season, merge_tire_query

WINTER = {"sizes": ["205/55 R16"], "season": "winter"}


@pytest.mark.parametrize(
    "bot",
    [
        "Які шини шукаєте — літні чи зимові?",
        "Підкажіть сезон?",
        "Є 205/55 R16. Летние или зимние?",
    ],
)
def test_a_season_question_is_one(bot: str) -> None:
    assert _bot_asked_season(bot)


@pytest.mark.parametrize(
    "bot",
    [
        "Є зимові Bridgestone Blizzak за 3200 гривень. Які вам?",
        "Зимові у розмірі 205/55 R16. Шиповані чи липучка? Який бренд?",
        "",
    ],
)
def test_a_season_word_outside_a_question_is_not(bot: str) -> None:
    assert not _bot_asked_season(bot)


def test_any_brand_after_an_offer_list_keeps_the_winter_season() -> None:
    out = merge_tire_query(
        WINTER,
        "шиповані, бренд будь-який, без побажань",
        last_bot_text="Є зимові Nokian за 3000 гривень. Шиповані чи липучка?",
    )
    assert out["season"] == "winter"
    assert out["nail"] == "studded"


def test_any_after_the_season_question_is_any_season() -> None:
    out = merge_tire_query({"sizes": ["205/55 R16"]}, "будь-який", last_bot_text="Літні чи зимові?")
    assert out["season"] == "any"
