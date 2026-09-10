"""Wave 14: slot-pin detector — customer picks a time from the offered list."""

import pytest

from src.agent.time_detect import (
    bot_asked_for_time,
    bot_listed_slots,
    detect_time_choice,
    hour_only_allowed,
)

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


def test_bare_hour_takes_the_slot_on_the_hour():
    """«на чотирнадцяту» names 14:00, even though 14:20 shares the hour.

    Reversed on 2026-09-10. Calling this ambiguous read the caller as vaguer
    than they were — a half-past pick is spoken «чотирнадцята двадцять». Call
    `431e60fb` offered the full half-hour grid, the caller said «о 12», this
    returned None, and the resulting `parser_null` charge escalated a fully
    collected, customer-confirmed booking to an operator.
    """
    offered = ["14:00", "14:20"]
    assert detect_time_choice("на чотирнадцяту", offered, allow_hour_only=True) == "14:00"


def test_bare_hour_stays_ambiguous_without_a_slot_on_the_hour():
    """Nothing at 14:00, and two candidates left — still a coin flip."""
    offered = ["14:20", "14:40"]
    assert detect_time_choice("на чотирнадцяту", offered, allow_hour_only=True) is None


@pytest.mark.parametrize("text", ["11:20", "на 12-20", "9 20", "на 11 на 11:30"])
def test_named_minutes_are_never_rounded_to_the_hour(text):
    """An unavailable `HH:MM` must not be answered with `HH:00`.

    The widening above only applies to a *bare* hour. Measured over 30 days of
    prod turns, dropping this guard turned «11:20», «15:20», «на 12-20» and
    «9 20» into the top of their hour — a time nobody asked for, pinned
    silently. Saying «такого слоту немає» is the correct answer here.
    """
    offered = ["09:00", "11:00", "11:40", "12:00", "15:00"]
    assert detect_time_choice(text, offered, allow_hour_only=True) is None


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


class TestBotAskedForTime:
    """The bot asks Krok 4 bare as often as it reads the list out.

    `bot_listed_slots` answers «did the bot read the times», which is not the
    only turn on which a bare number is an hour. Every string below is a real
    bot turn from the 30 days to 2026-09-10; «О котрій зручніше?» is the
    canonical template (`prompts.py:2016`) and is what call `431e60fb` died on.
    Across 2328 bot turns this widens 24 of them, and all 24 are questions
    about which hour the caller wants.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            "О котрій зручніше?",
            "Знайшла точку на Харківському шосе, 165. О котрій зручний час?",
            "Записуємо на шиномонтаж у Дніпрі, провулок Добровольців, один де. Який час зручний?",
            "На вул. Маршала Тимошенка, 7 на Оболоні. Який час ввечері вам зручний?",
            "Приймаю, шини привозите свої з собою. На яку годину записувати?",
            "На якій годині вам зручно? Є вільні слоти о 9:00, 9:40, 10:20, 11:00.",
            "Перепрошую, сталася помилка при записі. Спробую ще раз. О котрій годині зручніше?",
        ],
    )
    def test_real_time_questions(self, utterance):
        assert bot_asked_for_time(utterance)

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            "На яку дату вас записати?",
            "Записала на 14:20.",
            "Яка марка вашого автомобіля?",
            # Not a question about *which* hour — it proposes one.
            "О 11:00 є вільний час на 12 вересня. Приймаємо цю годину?",
            # The bare noun «час» is not enough, or these would qualify.
            "Вільний час на 12 вересня уточнюю, зачекайте.",
            "Слоту на 12:20 немає, вільних часів на цю дату не залишилось.",
            "Для сімнадцятого діаметра комплексний шиномонтаж коштує "
            "триста дев'яносто шість гривень.",
            "Готово, записала на дев'яте вересня о одинадцятій.",
        ],
    )
    def test_not_a_time_question(self, utterance):
        assert not bot_asked_for_time(utterance)

    def test_askijs_time_is_not_a_time_question(self):
        """«якийсь час» must not read as «який час» — the stem is followed by «сь».

        Call `cbb41e0d` rendered «коштує якийсь час повідомлю, за колесо», so
        this shape does reach the transcript.
        """
        assert not bot_asked_for_time("Зачекайте якийсь час, будь ласка.")


class TestHourOnlyAllowed:
    """The single authority both call sites use, so the rule cannot drift."""

    def test_either_signal_is_enough(self):
        assert hour_only_allowed("Є 9:20, 10:20, 11:20. Який зручніше?")
        assert hour_only_allowed("О котрій зручніше?")

    def test_neither_signal(self):
        assert not hour_only_allowed("Яка марка вашого автомобіля?")


def test_dictated_phone_number_is_not_a_slot():
    offered = ["09:00", "10:20"]
    assert detect_time_choice("нуль дев'ять нуль сім три два один", offered) is None
    assert detect_time_choice("мій номер 0970932120", offered) is None


GRID = ["09:00", "09:40", "10:20", "11:00", "13:00", "14:00", "14:20", "15:00", "17:00"]


class TestTwelveHourClock:
    """Call `8abd8557` (2026-09-10) — «давайте на два» meant 14:00.

    The bot had just read out the 17 September list. This detector returned
    ``None``, the LLM answered «Час 10:20 прийнято», and the caller's correction
    («еще раз временно зовите я хотел на два часа дня») ended the call at an
    operator. Two of the day's calls died in TIME.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("давайте на два", "14:00"),
            ("я хотел на два часа дня", "14:00"),
            ("на другу", "14:00"),
            ("о другій", "14:00"),
            ("на першу", "13:00"),
            ("на третю", "15:00"),
            ("на п'яту", "17:00"),
            ("на 5 вечера", "17:00"),
            ("на 5", "17:00"),
        ],
    )
    def test_afternoon_hour_spoken_on_a_twelve_hour_clock(self, text, expected):
        assert detect_time_choice(text, GRID, allow_hour_only=True) == expected

    def test_the_afternoon_reading_still_has_to_be_on_offer(self):
        """The mapping widens what is proposed; membership stays the authority."""
        assert detect_time_choice("на шосту", GRID, allow_hour_only=True) is None
        assert detect_time_choice("на сьому", GRID, allow_hour_only=True) is None

    def test_minutes_survive_the_mapping(self):
        assert detect_time_choice("на два двадцять", GRID) == "14:20"
        assert detect_time_choice("на два тридцять", GRID) is None

    def test_a_bare_afternoon_hour_still_needs_widening(self):
        """Same gate as any other bare hour — «на два» could be two of something."""
        assert detect_time_choice("на два", GRID) is None

    def test_the_lone_slot_in_the_hour_is_taken(self):
        """`8abd8557`'s real grid: 40-minute steps, so hour 14 holds only 14:20."""
        forty = ["09:00", "09:40", "10:20", "11:00", "11:40", "12:20", "13:00", "13:40", "14:20"]
        assert detect_time_choice("давайте на два", forty, allow_hour_only=True) == "14:20"

    def test_a_morning_hour_is_unaffected(self):
        for text, expected in (("на дев'яту", "09:00"), ("о 11", "11:00")):
            assert detect_time_choice(text, GRID, allow_hour_only=True) == expected

    def test_the_mapping_is_stated_in_full(self):
        """Pinned as a table so the widening cannot quietly grow.

        Only 1-7 are re-read. An hour that is already inside the working day
        keeps its single reading: «на вісім» is 08:00 and must not also offer
        20:00, or the detector would start guessing between two hours the
        caller distinguished perfectly well.
        """
        from src.agent.time_detect import _hour_variants

        afternoon = [[13], [14], [15], [16], [17], [18], [19]]
        assert [_hour_variants(n) for n in range(1, 8)] == afternoon
        assert all(_hour_variants(n) == [n] for n in range(8, 21)), "one reading, not two"
        assert _hour_variants(0) == []
        assert _hour_variants(21) == []

    def test_an_hour_inside_the_working_day_is_not_re_read(self):
        assert detect_time_choice("на вісім", ["20:00", "09:00"], allow_hour_only=True) is None


COUNT_GRID = ["09:00", "13:00", "14:00", "16:00", "17:00"]


class TestWheelCountIsNotAnHour:
    """2 and 4 are the two commonest wheel counts and two afternoon hours.

    The booking flow never asks how many wheels — it books a set of four
    (`prompts.py:450`) — but the caller volunteers it, and the price flow reads
    it as a multiplier (`prompts.py:706`). Without the guard «два колеса» pins
    14:00.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "два колеса",
            "на 4 колеса",
            "чотири колеса",
            "тільки два колеса перевзути",
            "це за 4 колеса чи за одне",
            "за чотири шини",
        ],
    )
    def test_a_count_never_pins_a_slot(self, text):
        assert detect_time_choice(text, COUNT_GRID, allow_hour_only=True) is None

    def test_the_guard_is_load_bearing(self):
        """Without the strip the same phrase resolves — asserted, not assumed."""
        from src.agent.time_detect import _QUANTITY_RE, _normalize

        stripped = _QUANTITY_RE.sub(" ", _normalize("два колеса"))
        assert "колеса" not in stripped
        assert detect_time_choice("два колеса", COUNT_GRID, allow_hour_only=True) is None
        assert detect_time_choice("два", COUNT_GRID, allow_hour_only=True) == "14:00"

    def test_an_hour_next_to_an_unrelated_noun_still_counts(self):
        assert detect_time_choice("давайте о другій", COUNT_GRID, allow_hour_only=True) == "14:00"
