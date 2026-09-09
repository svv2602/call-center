"""Wave 14: slot-pin detector — customer picks a time from the offered list."""

import pytest

from src.agent.time_detect import bot_listed_slots, detect_time_choice

OFFERED = ["09:20", "10:20", "11:20", "12:20", "13:20", "14:20", "15:40"]


@pytest.mark.parametrize(
    "text,expected",
    [
        # Root case, call 2026-09-07: STT mangled «чотирнадцята двадцять».
        ("14 это 20", "14:20"),
        ("на 14:20", "14:20"),
        ("давайте чотирнадцята двадцять", "14:20"),
        ("чотирнадцяту двадцять будь ласка", "14:20"),
        ("о десятій двадцять", "10:20"),
        ("десять двадцять", "10:20"),
        ("пятнадцять сорок", "15:40"),
        ("1420", "14:20"),
        ("можна на дванадцяту двадцять", "12:20"),
    ],
)
def test_detects_offered_time(text, expected):
    assert detect_time_choice(text, OFFERED) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "мені треба шиномонтаж",
        "14 30",  # plausible time, but not offered
        "о шістнадцятій",  # no 16:xx in the list
        "17:00",
    ],
)
def test_never_invents_a_slot(text):
    assert detect_time_choice(text, OFFERED) is None


def test_diameter_is_not_a_time():
    """«R14» must not pin 14:20 — Wave 12 guard would be undone otherwise."""
    assert detect_time_choice("у мене R14", OFFERED) is None
    assert detect_time_choice("ер 14", OFFERED) is None


def test_bare_hour_needs_explicit_widening():
    offered = ["09:00", "14:20"]
    assert detect_time_choice("давайте на чотирнадцяту", offered) is None
    assert (
        detect_time_choice("давайте на чотирнадцяту", offered, allow_hour_only=True)
        == "14:20"
    )


def test_bare_hour_stays_ambiguous_when_several_slots_share_it():
    offered = ["14:00", "14:20"]
    assert (
        detect_time_choice("на чотирнадцяту", offered, allow_hour_only=True) is None
    )


def test_composite_minutes_are_merged():
    offered = ["10:25", "10:20"]
    assert detect_time_choice("десята двадцять п'ять", offered) == "10:25"


def test_empty_offered_list_is_a_noop():
    assert detect_time_choice("14:20", []) is None


def test_bot_listed_slots():
    assert bot_listed_slots("Є 9:20, 10:20, 11:20. Який зручніше?")
    assert not bot_listed_slots("На яку дату вас записати?")
    assert not bot_listed_slots("Записала на 14:20.")
    assert not bot_listed_slots("")


class TestBotListedSlotsInWords:
    """TTS reads the list out in words, so it carries no `HH:MM` token at all.

    Call `f2bec2d6` (2026-09-07) died in TIME because of this: the bot offered
    «тринадцять», the caller answered «давайте на 13», and `allow_hour_only`
    was off, so the pick never matched.
    """

    def test_spelled_out_list(self):
        assert bot_listed_slots(
            "Слоту на дванадцять двадцять немає. З переліку вільні: дев'ять, "
            "десять двадцять, одинадцять сорок, тринадцять"
        )

    def test_spelled_out_list_after_a_spoken_date(self):
        assert bot_listed_slots(
            "На дванадцяте вересня вільний час: дев'ята, дев'ята сорок, десята двадцять"
        )

    def test_typographic_apostrophe_is_normalised(self):
        assert bot_listed_slots("Вільний час: девʼята, десята двадцять")

    def test_single_slot_day_is_still_a_list(self):
        assert bot_listed_slots(
            "На 11 вересня онлайн-запис закритий. Вільний час на 12 вересня: 15:00."
        )


class TestBotListedSlotsStaysOff:
    """`allow_hour_only` must not follow a price or a booking summary.

    Those turns are full of spoken hours («о 11:00», «сімнадцятого діаметра»),
    and a bare number right after one of them is far more likely a diameter —
    the case the flag exists for.
    """

    def test_krok8_confirmation_summary(self):
        assert not bot_listed_slots(
            "Наталя, перевіримо: дев'ятого вересня о 11:00, м. Дніпро, "
            "провулок Добровольців, один де, зелений Жигулі. Підтверджуєте?"
        )

    def test_price_quote(self):
        assert not bot_listed_slots(
            "Для сімнадцятого діаметра у місті Дніпро комплексний шиномонтаж "
            "легкових авто коштує триста дев'яносто шість гривень."
        )

    def test_booking_confirmed(self):
        assert not bot_listed_slots(
            "Готово, записала на дев'яте вересня о одинадцятій на провулку Добровольців."
        )

    def test_marker_without_any_hour(self):
        """The bot asked for an approximate hour — it listed nothing."""
        assert not bot_listed_slots(
            "Є вільні часи ближче до вечора: скажіть, яку годину приблизно ви хочете?"
        )

    def test_date_number_after_the_marker_is_not_an_hour(self):
        """«12 вересня» is a date; without stripping it the rule would fire
        whenever the day of month happened to be ≤ 20."""
        assert not bot_listed_slots("Вільний час на 12 вересня уточнюю, зачекайте.")

    def test_hour_named_before_the_marker_does_not_count(self):
        """The refused hour is not an offer — nothing was listed after it."""
        assert not bot_listed_slots("Слоту на 12:20 немає, вільних часів на цю дату не залишилось.")

    def test_single_proposal_is_not_a_list(self):
        """Corpus turn: the one hour precedes the marker, and «12 вересня» is
        a date — so the bot proposed a time rather than reading out a list."""
        assert not bot_listed_slots("О 11:00 є вільний час на 12 вересня. Приймаємо цю годину?")


def test_dictated_phone_number_is_not_a_slot():
    offered = ["09:00", "10:20"]
    assert detect_time_choice("нуль дев'ять нуль сім три два один", offered) is None
    assert detect_time_choice("мій номер 0970932120", offered) is None
