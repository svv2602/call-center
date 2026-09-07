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


def test_dictated_phone_number_is_not_a_slot():
    offered = ["09:00", "10:20"]
    assert detect_time_choice("нуль дев'ять нуль сім три два один", offered) is None
    assert detect_time_choice("мій номер 0970932120", offered) is None
