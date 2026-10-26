"""Tests for color auto-detection (Wave 5, replaces substring hint list).

Regression seed: call dd3dd368 2026-09-03 — «Червоны» (Russian, plural
form / STT mutation of «Красный») was NOT matched by the old substring
list because it only contained «червоний» (UA nominative masculine).
Bot said «не розчула колір» twice, then wrongly triggered the «колір не
назвали» escape hatch.
"""

from __future__ import annotations

import pytest

from src.agent.color_detect import detect_color


class TestBaseColorsUkrainian:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("червоний", "червоний"),
            ("червона", "червоний"),
            ("червоне", "червоний"),
            ("червоні", "червоний"),
            ("білий", "білий"),
            ("біла", "білий"),
            ("біле", "білий"),
            ("чорний", "чорний"),
            ("чорна", "чорний"),
            ("сірий", "сірий"),
            ("сіра", "сірий"),
            ("синій", "синій"),
            ("синя", "синій"),
            ("синє", "синій"),
            ("зелений", "зелений"),
            ("зелена", "зелений"),
            ("жовтий", "жовтий"),
            ("жовта", "жовтий"),
            ("бежевий", "бежевий"),
        ],
    )
    def test_ua_inflections(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestBaseColorsRussian:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("красный", "червоний"),
            ("красная", "червоний"),
            ("красное", "червоний"),
            ("красную", "червоний"),
            ("белый", "білий"),
            ("белая", "білий"),
            ("белое", "білий"),
            ("чёрный", "чорний"),
            ("черный", "чорний"),
            ("чёрная", "чорний"),
            ("серый", "сірий"),
            ("серая", "сірий"),
            ("серое", "сірий"),
            ("синий", "синій"),
            ("синяя", "синій"),
            ("зелёный", "зелений"),
            ("зеленый", "зелений"),
            ("жёлтый", "жовтий"),
            ("желтый", "жовтий"),
        ],
    )
    def test_ru_inflections(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestSTTMutations:
    """Regression: call dd3dd368 turn 57/65."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("червоны", "червоний"),
            ("Червоны", "червоний"),
            ("червона", "червоний"),
            ("червоно", "червоний"),
            ("червонное", "червоний"),
        ],
    )
    def test_chervony_variants(self, text: str, expected: str) -> None:
        assert detect_color(text.lower()) == expected

    def test_case_insensitive(self) -> None:
        assert detect_color("Красный") is None  # Must lowercase before calling
        assert detect_color("красный") == "червоний"


class TestSilverVsGrey:
    """«сер» and «серебр» share a prefix — silver must win."""

    def test_silver_ru(self) -> None:
        assert detect_color("серебристый") == "срібний"
        assert detect_color("серебряный") == "срібний"

    def test_silver_ua(self) -> None:
        assert detect_color("срібний") == "срібний"
        assert detect_color("сріблястий") == "срібний"

    def test_grey_ru(self) -> None:
        assert detect_color("серый") == "сірий"

    def test_silver_before_grey(self) -> None:
        # «серебристый» must match «серебрист» (silver) not «сер» (grey).
        assert detect_color("серебристый металлик") == "срібний"


class TestCompoundColors:
    def test_mokry_asfalt(self) -> None:
        assert detect_color("мокрий асфальт") == "мокрий асфальт"
        assert detect_color("мокрый асфальт") == "мокрий асфальт"

    def test_temno_prefix(self) -> None:
        assert detect_color("темно-синій") == "темний"
        assert detect_color("темно синій") == "темний"

    def test_yaskravo_prefix(self) -> None:
        assert detect_color("яскраво-червоний") == "яскравий"


class TestFancyColors:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("перламутр", "перламутр"),
            ("антрацит", "антрацит"),
            ("маренго", "маренго"),
            ("шампань", "шампань"),
            ("шоколад", "шоколадний"),
            ("шоколадний", "шоколадний"),
            ("графіт", "графіт"),
            ("графит", "графіт"),
            ("оливковий", "оливковий"),
            ("титан", "титан"),
            ("платина", "платина"),
            ("металік", "металік"),
            ("металлик", "металік"),
            ("гранат", "гранатовий"),
            ("вишневий", "вишневий"),
        ],
    )
    def test_fancy_words(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected


class TestFalsePositives:
    """City names and other words that share a color root must NOT match."""

    def test_cherkasy_not_black(self) -> None:
        # Черкаси starts with «черк» — no color root should fire.
        assert detect_color("черкаси") is None
        assert detect_color("хочу в черкаси") is None

    def test_chernihiv_not_black(self) -> None:
        assert detect_color("чернігів") is None
        assert detect_color("чернигов") is None

    def test_krasnoyarsk_not_red(self) -> None:
        assert detect_color("красноярск") is None
        assert detect_color("з красноярска") is None

    def test_bila_tserkva_not_white(self) -> None:
        assert detect_color("біла церква") is None

    def test_sergey_not_grey(self) -> None:
        assert detect_color("сергей") is None
        assert detect_color("сергій") is None

    def test_serpen_not_grey(self) -> None:
        # «серпень» (August) starts with «сер» but is not grey.
        assert detect_color("серпень") is None

    def test_belka_not_white(self) -> None:
        assert detect_color("белка") is None


class TestInSentence:
    """Colors embedded in fuller customer utterances."""

    def test_polite_answer(self) -> None:
        assert detect_color("білий, будь ласка") == "білий"

    def test_with_prefix(self) -> None:
        assert detect_color("у мене червоний") == "червоний"

    def test_with_model(self) -> None:
        # «біла Toyota» — should match «біла».
        assert detect_color("біла toyota") == "білий"

    def test_answering_with_noise(self) -> None:
        # STT sometimes prepends filler words.
        assert detect_color("ну красный, наверное") == "червоний"


class TestNoMatch:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "не пам'ятаю",
            "не знаю",
            "забув",
            "алло",
            "мгм",
            "1234",
            "aa1234bb",
            "toyota",
            "mercedes",
        ],
    )
    def test_no_color(self, text: str) -> None:
        assert detect_color(text) is None


# ---------------------------------------------------------------------------
# Calendar and service words said on the DATE step (2026-10-26).
#
# The broad pass (`compound_parse`) runs this detector on every customer turn,
# so a date answer that reads as a colour fills the COLOR slot and the question
# is never asked. Two October bookings went to 1C as «жовтий» from «на 17
# жовтня». The guard is checked over the whole calendar, not over October.
# ---------------------------------------------------------------------------

_MONTHS_UA = [
    "січень",
    "січня",
    "січні",
    "лютий",
    "лютого",
    "лютому",
    "березень",
    "березня",
    "березні",
    "квітень",
    "квітня",
    "квітні",
    "травень",
    "травня",
    "травні",
    "червень",
    "червня",
    "червні",
    "липень",
    "липня",
    "липні",
    "серпень",
    "серпня",
    "серпні",
    "вересень",
    "вересня",
    "вересні",
    "жовтень",
    "жовтня",
    "жовтні",
    "жовтневий",
    "листопад",
    "листопада",
    "листопаді",
    "грудень",
    "грудня",
    "грудні",
]
_MONTHS_RU = [
    "январь",
    "января",
    "январе",
    "февраль",
    "февраля",
    "феврале",
    "март",
    "марта",
    "марте",
    "апрель",
    "апреля",
    "апреле",
    "май",
    "мая",
    "мае",
    "июнь",
    "июня",
    "июне",
    "июль",
    "июля",
    "июле",
    "август",
    "августа",
    "августе",
    "сентябрь",
    "сентября",
    "сентябре",
    "октябрь",
    "октября",
    "октябре",
    "ноябрь",
    "ноября",
    "ноябре",
    "декабрь",
    "декабря",
    "декабре",
]
_WEEKDAYS = [
    "понеділок",
    "понеділка",
    "вівторок",
    "вівторка",
    "середа",
    "середу",
    "середи",
    "четвер",
    "четверга",
    "п'ятниця",
    "п'ятницю",
    "субота",
    "суботу",
    "неділя",
    "неділю",
    "понедельник",
    "понедельника",
    "вторник",
    "вторника",
    "среда",
    "среду",
    "четверг",
    "пятница",
    "пятницу",
    "суббота",
    "субботу",
    "воскресенье",
]


class TestCalendarWordsAreNotColours:
    def test_enumeration_is_complete(self) -> None:
        # 12 months x 3 forms (+ «жовтневий» in UA) — a shrunken list must
        # fail loudly here, not pass quietly below.
        assert len(_MONTHS_UA) == 37
        assert len(_MONTHS_RU) == 36

    @pytest.mark.parametrize("word", _MONTHS_UA + _MONTHS_RU + _WEEKDAYS)
    def test_bare_word(self, word: str) -> None:
        assert detect_color(word) is None

    @pytest.mark.parametrize("word", _MONTHS_UA + _MONTHS_RU)
    def test_in_a_date_answer(self, word: str) -> None:
        assert detect_color(f"на 17 {word} да") is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("жовтий, на 17 жовтня", "жовтий"),
            ("на 17 жовтня, машина жовта", "жовтий"),
            ("у жовтні жовтий", "жовтий"),
            ("сірий, до сервісу", "сірий"),
            ("серый, на сервис", "сірий"),
            ("сіра, 5 серпні", "сірий"),
            ("сині, у мене нема сина", "синій"),
            ("синя", "синій"),
            ("синю", "синій"),
            ("синього", "синій"),
            ("белый, біля каравану", "білий"),
            ("біленька білочка", "білий"),
        ],
    )
    def test_colour_next_to_an_excluded_word(self, text: str, expected: str) -> None:
        # Exclusions are substring cuts: each one must leave every form of
        # the colour it shares a root with intact.
        assert detect_color(text) == expected

    @pytest.mark.parametrize(
        ("forms", "expected"),
        [
            (
                "жовтий жовта жовте жовті жовтого жовтої жовтому жовтим жовтих жовтою жовту",
                "жовтий",
            ),
            ("желтый желтая желтое желтые желтого желтой желтому желтым желтых желтую", "жовтий"),
            ("сірий сіра сіре сірі сірого сірої сірому сірим сірих сірою сіру", "сірий"),
            ("серый серая серое серые серого серой серому серым серых серую", "сірий"),
            ("синій синя синє сині синього синьої синьому синім синіх синьою синю", "синій"),
            ("синий синяя синее синие синего синей синему синим синих синюю", "синій"),
            ("білий біла біле білі білого білої білому білим білих білою білу", "білий"),
            ("белый белая белое белые белого белой белому белым белых белую", "білий"),
        ],
    )
    def test_every_form_of_a_colour_sharing_a_root(self, forms: str, expected: str) -> None:
        # Exhaustive over the four colours the new exclusions sit next to.
        for form in forms.split():
            assert detect_color(form) == expected, form

    @pytest.mark.parametrize(
        "text",
        [
            "у мене нема сина",
            "для сину",
            "приїду з сином",
            "синові машина",
            "запишіть до сервісу",
            "сервис",
            "сервер",
            "білочка",
            "беляк",
            "постельное белье",
        ],
    )
    def test_every_excluded_word_form(self, text: str) -> None:
        assert detect_color(text) is None


class TestCustomerCorpus20261026:
    """Every `detect_color` hit on 4496 customer turns, 90 days to 2026-10-26.

    The false ones must go, the real ones must keep the colour they had.
    «гранат это 20» (1x) is left out on purpose — disputed, not in R11.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "на 17 жовтня",
            "на 17 жовтня вересня",
            "17 жовтня",
            "на 28 жовтня да",
            "я хочу записатися до сервісу",
            "добрий день хочу записатися до сервісу",
            "записали на сервис",
            "я хочу записать эти до сервиса на 17 днепродана из кошачьи 69",
            "затащить на сервер",
            "в мене нема сина зберігає",
            "узнать сину",
            "добрый день апаре обувку белья передовую",
            "білочка 100 почему 11 на завтра на 6",
            "мне потребно держать монтажу там беляк каравану моменту зашлифовывает",
        ],
    )
    def test_false_hit_is_gone(self, text: str) -> None:
        assert detect_color(text) is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("автомобиля желтый", "жовтий"),
            ("автомобіль renault синій", "синій"),
            ("бежевий колір", "бежевий"),
            ("белая машина", "білий"),
            ("белый", "білий"),
            ("білий", "білий"),
            ("білих", "білий"),
            ("блакитний", "блакитний"),
            ("желтый", "жовтий"),
            ("жовтий", "жовтий"),
            ("жовтих", "жовтий"),
            ("зелене", "зелений"),
            ("зелений", "зелений"),
            ("зеленый", "зелений"),
            ("колер белый", "білий"),
            ("колер синий", "синій"),
            ("колер черный", "чорний"),
            ("колір автомобіля блакитний", "блакитний"),
            ("колір зелений", "зелений"),
            ("колір червоний автомобіль mazda", "червоний"),
            ("колір чорний", "чорний"),
            ("коричневий", "коричневий"),
            ("коричневый", "коричневий"),
            ("красний", "червоний"),
            ("на серый дух за париже", "сірий"),
            ("на серый", "сірий"),
            ("не жовтий синій", "синій"),
            ("нет будет белый порш", "білий"),
            ("ні не підтверджую сірий джип", "сірий"),
            ("номер не пам'ятаю колір червоний", "червоний"),
            ("номер я не помню колір червоний", "червоний"),
            ("помаранчеве", "помаранчевий"),
            ("помаранчевий", "помаранчевий"),
            ("помаранчевой", "помаранчевий"),
            ("помаранчивый", "помаранчевий"),
            ("рожевий", "рожевий"),
            ("серебристый", "срібний"),
            ("серый", "сірий"),
            ("серы", "сірий"),
            ("синий цвет автомобиля", "синій"),
            ("синій колір", "синій"),
            ("синій міні купер", "синій"),
            ("синій", "синій"),
            ("сині", "синій"),
            ("сірий", "сірий"),
            ("сріблястий", "срібний"),
            ("срібний", "срібний"),
            ("фиолетовий", "фіолетовий"),
            ("фиолетовый", "фіолетовий"),
            ("червоний", "червоний"),
            ("червонный", "червоний"),
            ("червоны", "червоний"),
            ("черный", "чорний"),
            ("чёрный", "чорний"),
            ("чорний", "чорний"),
            ("chevrolet aveo синій", "синій"),
            ("oler белый", "білий"),
            ("porsche cayenne черный", "чорний"),
            ("renault синій", "синій"),
            ("skoda octavia красная", "червоний"),
        ],
    )
    def test_real_colour_is_kept(self, text: str, expected: str) -> None:
        assert detect_color(text) == expected
