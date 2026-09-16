"""The caller may repaint a colour the bot read back to them.

Two live calls on 2026-09-16 ended with the wrong colour in 1C:

  * `79c1d7c5` — the bot never asked for the colour at all; «сірий» came off
    the customer profile. It recapped «сірий Mini Cooper», heard «синій Міні
    Купер», and answered «Колір авто вже зафіксований як сірий». Twice.
  * `21d0b864` — «Ні, не підтверджую, сірий джип» → the bot repeated its
    жовтий recap verbatim.

Both are the same defect: the pin was write-once, so the only turn on which
the caller can see the value is the one turn on which they cannot change it.
Collected is not settled.

The tests run the real `_transcript_processor_loop` through `Harness` rather
than calling a helper, because the value being asserted on is the one the
pipeline writes — a corpus test over `detect_color` passes either way (see
`codetrap_corpus_tests_dont_cover_the_wiring`).
"""

from __future__ import annotations

import uuid

from src.core.call_session import CallSession

from .test_pipeline_fsm_wire import Harness, fsm_flags

# The recap from `79c1d7c5`, trimmed to the part the detector reads.
RECAP_GREY = "Наталя, перевіримо: Дніпро, вулиця Набережна, сірий Mini Cooper. Підтверджуєте?"
KROK_5 = "Назвіть, будь ласка, колір вашого авто."


def session_with(pinned: str | None, *, bot_said: str) -> CallSession:
    session = CallSession(uuid.uuid4())
    session.fitting_plate = pinned
    session.add_assistant_turn(bot_said)
    return session


async def drive(session: CallSession, said: str) -> CallSession:
    h = Harness(session=session)
    with fsm_flags(enabled=False):
        await h.run(said)
    return h.session


class TestAnEmptySlotTakesTheAnswer:
    async def test_krok_5_answer_is_pinned(self) -> None:
        session = await drive(session_with(None, bot_said=KROK_5), "синій")
        assert session.fitting_plate == "синій"
        assert session.fitting_color_corrected is False

    async def test_a_weekday_answered_to_the_colour_question_is_not_a_colour(
        self,
    ) -> None:
        """«середу» shares a root with «сірий» and the gate is wide open here:
        the bot did ask for the colour, so only the detector stands between a
        caller changing the subject and a grey car in 1C."""
        session = await drive(session_with(None, bot_said=KROK_5), "а можна на середу?")
        assert session.fitting_plate is None

    async def test_a_landmark_answered_to_the_colour_question_is_not_a_colour(
        self,
    ) -> None:
        """Same shape: «біля» shares a root with «білий»."""
        session = await drive(
            session_with(None, bot_said=KROK_5), "мені зручно біля 15 години"
        )
        assert session.fitting_plate is None


class TestAReadBackColourCanBeCorrected:
    async def test_the_recap_colour_is_replaced(self) -> None:
        """`79c1d7c5`: «сірий Mini Cooper» → «синій Міні Купер»."""
        session = await drive(
            session_with("сірий", bot_said=RECAP_GREY), "синій Міні Купер"
        )
        assert session.fitting_plate == "синій"
        assert session.fitting_color_corrected is True

    async def test_an_explicit_refusal_still_carries_the_new_colour(self) -> None:
        """`21d0b864`: the «ні» is not the payload — «сірий» is."""
        session = await drive(
            session_with("жовтий", bot_said="Перевіримо: жовтий джип. Підтверджуєте?"),
            "ні не підтверджую сірий джип",
        )
        assert session.fitting_plate == "сірий"
        assert session.fitting_color_corrected is True

    async def test_confirming_the_same_colour_changes_nothing(self) -> None:
        session = await drive(
            session_with("сірий", bot_said=RECAP_GREY), "так, сірий, все вірно"
        )
        assert session.fitting_plate == "сірий"
        assert session.fitting_color_corrected is False


class TestAPinTheBotDidNotReadBackHolds:
    async def test_a_colour_on_the_brand_question_does_not_repaint(self) -> None:
        """The brand question opens the detector — the pin still has to hold.

        «Сірий Опель» on a brand turn is the caller describing the car they
        already named a colour for, not correcting it. Only a colour the bot
        asked for or read back may move a filled pin.
        """
        session = await drive(
            session_with("червоний", bot_said="Яка марка вашого авто?"),
            "Опель, сірий металік",
        )
        assert session.fitting_plate == "червоний"
        assert session.fitting_color_corrected is False

    async def test_an_unrelated_turn_never_reaches_the_detector(self) -> None:
        session = await drive(
            session_with("червоний", bot_said="На яку годину вас записати?"),
            "у сірому будинку навпроти",
        )
        assert session.fitting_plate == "червоний"

    async def test_a_plate_read_back_does_not_become_a_colour(self) -> None:
        """Preparse can still put a DSTU plate in this field.

        A plate in the recap is not the bot offering the *colour* for review,
        so a colour heard on that turn has no claim on the field.
        """
        session = await drive(
            session_with("АЕ1609НА", bot_said="Перевіримо: АЕ1609НА. Підтверджуєте?"),
            "так, синій",
        )
        assert session.fitting_plate == "АЕ1609НА"


class TestTheCorrectionSurvivesRedis:
    def test_the_flag_round_trips(self) -> None:
        """The correction and the book_fitting that honours it are different
        turns, and the Call Processor rebuilds from Redis in between."""
        session = CallSession(uuid.uuid4())
        session.fitting_plate = "синій"
        session.fitting_color_corrected = True

        revived = CallSession.from_dict(session.to_dict())

        assert revived.fitting_plate == "синій"
        assert revived.fitting_color_corrected is True

    def test_a_session_written_before_this_field_existed_reads_false(self) -> None:
        data = CallSession(uuid.uuid4()).to_dict()
        del data["fitting_color_corrected"]

        assert CallSession.from_dict(data).fitting_color_corrected is False
