"""`yes_no_parser` — CONFIRM. Both halves of the Wave 15 fix, and the hole.

Call `7462c08b` is the seed for the whole file. The bot's *last* turn was the
re-ask «Скажіть, будь ласка, "так" щоб підтвердити…», the customer answered
«так підтверджує», neither side was recognised, and the LLM announced
«Наталя, ви записані. СМС підтвердження надійде.» without ever calling
`book_fitting`.

Two properties follow, and both are tested here:

* the open-question marker is searched over a **window** of the bot's recent
  turns, not over the latest one — a filler re-ask sits between the question
  and the answer often enough to matter;
* a pure agreement of up to four tokens counts, but «так, але давайте на
  пʼятницю» carries new information and must reach the LLM unmodified.

Known hole (README §4, handed to Wave 6-B)
------------------------------------------
There is no negative detector in the repository, so «ні» comes back
`unresolved`, not `confirmed=False`. `TestNoNegativeDetector` pins that on
purpose: when Wave 6-B adds one, this file has to fail loudly rather than
quietly keep passing.
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.yes_no_parser import _BOT_TURN_WINDOW, PARSER
from src.core.call_session import CallSession, DialogTurn

#: Krok 8 summary + question, in the phrasing `_ASK_MARKERS` recognises.
CONFIRM_QUESTION = "Перевіримо: Наталя, білий Nissan, Оболонь, завтра о 14:00. Підтверджуєте запис?"
#: The re-ask from call 7462c08b. Contains «підтвердити», not «Підтверджуєте».
CONFIRM_REASK = 'Скажіть, будь ласка, "так" щоб підтвердити або "ні" щоб змінити.'
#: A filler that carries no marker at all — the case the window exists for.
FILLER = "Перепрошую, не розчула вас."


def ctx(text: str, *, bot: str = "") -> ParseContext:
    return ParseContext(customer_text=text, last_bot_utterance=bot)


def ctx_with_history(text: str, bot_turns: list[str]) -> ParseContext:
    """`bot_turns` oldest-first, as `dialog_history` stores them."""
    session = CallSession(channel_uuid=uuid.uuid4())
    history: list[DialogTurn] = []
    for turn in bot_turns:
        history.append(DialogTurn(speaker="assistant", content=turn))
        history.append(DialogTurn(speaker="user", content="…"))
    session.dialog_history = history
    return ParseContext(customer_text=text, session=session)


class TestAgreementToAnOpenQuestion:
    @pytest.mark.parametrize(
        "text",
        [
            "так",
            "да",
            "підтверджую",
            "так підтверджує",  # call 7462c08b — two words, the old regex missed it
            "вірно",
        ],
    )
    def test_pure_agreement_is_a_value(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=CONFIRM_QUESTION))
        assert outcome.status == "value"
        assert outcome.value is True
        assert outcome.confidence == 1.0

    def test_agreement_to_the_reask_counts_too(self) -> None:
        outcome = PARSER.parse(ctx("так підтверджує", bot=CONFIRM_REASK))
        assert outcome.status == "value"
        assert outcome.value is True


class TestTheBotTurnWindow:
    """A filler between the question and the answer must not close it."""

    def test_window_is_two_turns_like_the_pipeline(self) -> None:
        assert _BOT_TURN_WINDOW == 2

    def test_filler_after_the_question_still_leaves_it_open(self) -> None:
        outcome = PARSER.parse(ctx_with_history("так", [CONFIRM_QUESTION, FILLER]))
        assert outcome.status == "value"
        assert outcome.value is True

    def test_looking_only_at_the_latest_turn_would_have_missed_it(self) -> None:
        """The regression itself: the filler alone carries no marker."""
        from src.agent.confirm_detect import asked_for_confirmation

        assert asked_for_confirmation([FILLER]) is False
        assert asked_for_confirmation([FILLER, CONFIRM_QUESTION]) is True

    def test_a_question_three_turns_back_is_out_of_the_window(self) -> None:
        outcome = PARSER.parse(ctx_with_history("так", [CONFIRM_QUESTION, FILLER, "Гарного дня!"]))
        assert outcome.status == "not_mentioned"

    def test_without_a_session_it_falls_back_to_the_single_utterance(self) -> None:
        assert PARSER.parse(ctx("так", bot=CONFIRM_QUESTION)).status == "value"


class TestNoOpenQuestion:
    @pytest.mark.parametrize("bot", ["", "У якому місті вам зручно?", "Яка марка авто?"])
    def test_a_stray_yes_books_nothing(self, bot: str) -> None:
        """Agreement mid-flow is agreement to something else entirely."""
        outcome = PARSER.parse(ctx("так", bot=bot))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None

    def test_empty_text(self) -> None:
        assert PARSER.parse(ctx("   ", bot=CONFIRM_QUESTION)).status == "not_mentioned"


class TestAnswersCarryingNewInformation:
    @pytest.mark.parametrize(
        "text",
        [
            "так, але давайте на пʼятницю",
            "так, а можна о 18:00",
            "давайте, тільки адресу повторіть",
        ],
    )
    def test_more_than_a_bare_yes_is_unresolved(self, text: str) -> None:
        """It must reach the LLM unmodified, so the parser claims nothing."""
        outcome = PARSER.parse(ctx(text, bot=CONFIRM_QUESTION))
        assert outcome.status == "unresolved"
        assert outcome.value is None


class TestNoNegativeDetector:
    """README §4 hole — pinned as-is, handed to Wave 6-B.

    «ні» is a rejection, and a parser that returned `confirmed=False` would let
    the engine cancel. There is no negative detector in the repository, so the
    honest answer today is `unresolved` and the CONFIRM state re-asks.
    """

    @pytest.mark.parametrize("text", ["ні", "нет", "ні, не підтверджую"])
    def test_refusal_is_unresolved_not_false(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=CONFIRM_QUESTION))
        assert outcome.status == "unresolved"
        assert outcome.value is None
        assert outcome.value is not False, "Wave 6-B debt: no negative detector exists"

    def test_the_parser_only_ever_writes_true(self) -> None:
        values = {
            PARSER.parse(ctx(text, bot=CONFIRM_QUESTION)).value
            for text in ("так", "ні", "підтверджую", "нет", "може")
        }
        assert values == {True, None}


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "yes_no_parser"
        assert PARSER.field_name == "confirmed"

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {
            PARSER.parse(ctx(text, bot=bot)).status
            for text, bot in (
                ("так", CONFIRM_QUESTION),
                ("ні", CONFIRM_QUESTION),
                ("так", ""),
            )
        }
        assert statuses == {"value", "unresolved", "not_mentioned"}
