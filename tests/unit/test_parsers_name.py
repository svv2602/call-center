"""`name_parser` — the gate is the parser.

No NAME state exists in §2.1; the parser is registered as passive and runs on
every turn while `fsm_filled_fields["name"]` is empty. Two paths, matching §3.5:

* the bot just asked «Як до вас звертатися?» → `is_name_question` is true and
  `detect_name` runs on a short answer;
* otherwise only an explicit self-introduction counts, through
  `compound_parse._detect_name`.

The gate is the whole safety argument: ungated, `detect_name` accepts «Оболонь»
and «Завтра» as names, and a wrong name is how «Марина» — the bot's own name —
reached `book_fitting` on call fcfb26a9.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.name_parser import _ASKED_CONFIDENCE, PARSER

#: `_NAME_QUESTION_MARKERS` — the Krok 0 phrasing, verbatim.
NAME_QUESTION = "Як до вас звертатися?"
#: The «не розчула ім'я» re-ask, also a marker.
NAME_REASK = "Перепрошую, не розчула імʼя. Повторіть, будь ласка."


def ctx(text: str, *, bot: str = "") -> ParseContext:
    return ParseContext(customer_text=text, last_bot_utterance=bot)


class TestGateOpen:
    """The bot asked — a bare one-word answer is the name."""

    @pytest.mark.parametrize("text,expected", [("Юра", "Юра"), ("олена", "Олена")])
    def test_short_answer_is_taken(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=NAME_QUESTION))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == _ASKED_CONFIDENCE

    def test_the_reask_also_opens_the_gate(self) -> None:
        outcome = PARSER.parse(ctx("Юра", bot=NAME_REASK))
        assert outcome.status == "value"

    def test_the_bots_own_name_is_still_refused(self) -> None:
        """`_NAME_STOP_WORDS` — call fcfb26a9 put «Марина» into `book_fitting`."""
        assert PARSER.parse(ctx("Марина", bot=NAME_QUESTION)).status == "not_mentioned"

    @pytest.mark.parametrize("text", ["оператор", "Київ", "так", "шиномонтаж"])
    def test_stop_words_are_refused_even_with_the_gate_open(self, text: str) -> None:
        assert PARSER.parse(ctx(text, bot=NAME_QUESTION)).status == "not_mentioned"

    def test_a_sentence_is_not_a_name(self) -> None:
        outcome = PARSER.parse(ctx("я хочу дізнатися вартість", bot=NAME_QUESTION))
        assert outcome.status == "not_mentioned"


class TestGateClosed:
    """No question — only an explicit self-introduction counts."""

    @pytest.mark.parametrize("text", ["Юра", "Оболонь", "Завтра", "Ммм"])
    def test_a_bare_word_is_not_a_name_without_the_question(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("мене звати Олена", "Олена"),
            ("меня зовут Сергей", "Сергей"),
            ("моє ім'я Юра", "Юра"),
        ],
    )
    def test_explicit_introduction_is_taken(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == 0.9, "an inference from a phrase, not a literal"

    def test_the_name_stops_at_the_punctuation(self) -> None:
        """«мене звати Олена, білий Nissan, Запоріжжя» → «Олена», not the rest."""
        outcome = PARSER.parse(ctx("мене звати Олена, білий Nissan, Запоріжжя"))
        assert outcome.value == "Олена"

    def test_the_stop_word_list_still_applies_to_the_introduction(self) -> None:
        assert PARSER.parse(ctx("мене звати оператор")).status == "not_mentioned"

    def test_an_unrelated_bot_turn_does_not_open_the_gate(self) -> None:
        outcome = PARSER.parse(ctx("Юра", bot="У якому місті вам зручно?"))
        assert outcome.status == "not_mentioned"


class TestNotMentioned:
    @pytest.mark.parametrize("text", ["", "   ", "білий Nissan", "завтра о 14:00"])
    def test_nothing_name_like(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None


class TestUnresolvedIsUnreachable:
    def test_both_paths_grade_above_the_threshold(self) -> None:
        """`1.0` gated, `0.9` from the introduction — nothing lands below.

        The re-ask for a name is therefore always «I heard nothing», never «I
        heard something I could not pin». If a fuzzy path is ever added, this
        test is the reminder to give it its own re-ask.
        """
        statuses = {
            PARSER.parse(ctx(text, bot=bot)).status
            for text, bot in (
                ("Юра", NAME_QUESTION),
                ("мене звати Олена", ""),
                ("Оболонь", ""),
                ("", ""),
                ("Марина", NAME_QUESTION),
            )
        }
        assert statuses == {"value", "not_mentioned"}


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "name_parser"
        assert PARSER.field_name == "name"

    def test_it_is_passive(self) -> None:
        from src.agent.parsers.registry import PASSIVE_PARSERS

        assert "name_parser" in PASSIVE_PARSERS

    def test_no_state_points_at_it(self) -> None:
        """§3.4: NAME is not a state and this wave must not add one."""
        from src.agent.fitting_fsm import STATES

        assert "name_parser" not in {cfg.parser for cfg in STATES.values()}

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None
