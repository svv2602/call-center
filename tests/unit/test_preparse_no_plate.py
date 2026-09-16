"""The pre-parser must not read a licence plate out of a date and a time.

`preparse_fitting` used to extract a DSTU plate alongside the brand, and its
one consumer wrote it into `session.fitting_plate` — a field that has meant
*colour* since Krok 5 switched on 2026-08-18 and only kept its old name.

«на» is two letters of the plate alphabet and a preposition Ukrainians put in
front of both halves of a booking request, so «на 16.09 на 10:00» parsed as
prefix `НА` + digits `1609` + suffix `НА`. The fabricated plate marked the
colour ✅, the bot never asked for it, and `book_fitting` was then refused for
the missing brand — `2489d6bd` and `60ae3fdd` both ended with no booking.

A replay over 21 days and 264 prod calls found those two extractions and no
correct one, so the branch was deleted rather than tightened. These tests are
the tripwire: the phrasing is too ordinary for the pattern to stay gone by
luck, and a real plate still has to be ignored — 1C, not the caller's speech,
is where a plate comes from now (`update_customer_profile`,
`get_customer_bookings`).
"""

from __future__ import annotations

import uuid

import pytest

from src.agent.preparse import preparse_fitting
from src.core.call_session import CallSession

from .test_pipeline_fsm_wire import Harness, fsm_flags


class TestADateAndATimeAreNotAPlate:
    @pytest.mark.parametrize(
        "utterance",
        [
            # Verbatim from the two prod calls.
            "на 16 0 9 на 15",
            "понадкритий Я хочу на 16.09 на 11:40",
            # The same shape a caller reaches for every day.
            "запишіть мене на 16.09 на 10:00",
            "давайте на 20.09 на 14:00",
            "можна на 05.10 на 8:30",
        ],
    )
    def test_nothing_is_extracted(self, utterance: str) -> None:
        assert preparse_fitting(utterance) == {}


class TestAPlateIsNoLongerAFieldAtAll:
    @pytest.mark.parametrize(
        "utterance",
        # Every letter here is in the DSTU alphabet, so each one really
        # does reach the deleted branch — «AA 12 34 CD» does not, D is not
        # a plate letter, and such a param would pass either way.
        ["мій номер АА1234ВВ", "AA 12 34 CB", "АА-12-34-ВВ"],
    )
    def test_even_a_real_plate_is_ignored(self, utterance: str) -> None:
        """Krok 5 asks for the colour. A plate spoken here is not an answer to
        it, and there is no other session field for it to land in."""
        assert "plate" not in preparse_fitting(utterance)


class TestTheBrandStillWorks:
    """The deletion must not take the surviving half with it."""

    @pytest.mark.parametrize(
        ("utterance", "brand"),
        [
            ("на завтра, лексус, свої", "Lexus"),
            ("тойота", "Toyota"),
            ("у мене BMW", "BMW"),
            # A brand named in the very phrasing that used to fabricate a plate.
            ("на 16.09 на 10:00, мазда", "Mazda"),
        ],
    )
    def test_brand_is_still_extracted(self, utterance: str, brand: str) -> None:
        assert preparse_fitting(utterance) == {"brand": brand}


class TestThePipelineCannotPinAFabricatedPlate:
    """The wiring, not the predicate.

    A corpus test over `preparse_fitting` passes whether or not the pipeline
    still writes the result into the colour field, so the call site gets its
    own assertions (`codetrap_corpus_tests_dont_cover_the_wiring`).
    """

    async def test_the_colour_slot_stays_empty(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_assistant_turn("На яку дату записуємо?")
        h = Harness(session=session)
        with fsm_flags(enabled=False):
            await h.run("запишіть мене на 16.09 на 10:00")
        assert h.session.fitting_plate is None

    async def test_the_brand_is_still_gap_filled(self) -> None:
        session = CallSession(uuid.uuid4())
        session.add_assistant_turn("Яка марка вашого авто?")
        h = Harness(session=session)
        with fsm_flags(enabled=False):
            await h.run("тойота")
        assert h.session.fitting_vehicle_brand == "Toyota"

    async def test_a_brand_already_collected_is_not_trampled(self) -> None:
        session = CallSession(uuid.uuid4())
        session.fitting_vehicle_brand = "Nissan Qashqai"
        session.add_assistant_turn("Яка марка вашого авто?")
        h = Harness(session=session)
        with fsm_flags(enabled=False):
            await h.run("у сусіда тойота")
        assert h.session.fitting_vehicle_brand == "Nissan Qashqai"
