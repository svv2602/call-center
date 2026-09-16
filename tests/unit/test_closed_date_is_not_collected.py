"""A day 1С has no room on must not read as a collected date.

`get_fitting_slots` pins `session.selected_fitting_date` on every lookup,
including the ones that come back empty (`main.py:3110`), and the progress block
reads that field straight into its «Дата» row. So a closed day arrived at the
LLM looking exactly like a day the caller had chosen, and the block built on it
said: ✅ Дата, «слоти вже озвучено», «НЕ викликай get_fitting_slots з новою
датою», «ЄДИНА ДОЗВОЛЕНА ДІЯ: задай Крок 4 (Час)».

Call `60ae3fdd` 2026-09-16 is that block being obeyed. 1С closed 2026-09-16
twice with `today_too_late`; the bot said so; the caller insisted («понадкритий Я
хочу на 16.09 на 11:40») and the bot answered «На 16 вересня о 11:40 вільно,
приймаємо час?» — with no tool call in between. The model was not conceding to
the caller, it was following a block that contradicted the tool.

`a3de4520` 2026-09-05 is the same shape one step over: the block's stale date
drove a recap for «десяте вересня» while the caller was asking for the 7th.

The booking side was never at risk — three independent guards (Krok 3/4, date ∈
offered, time ∈ offered) refused every attempt, and over 21 days no confirmed
booking landed on a day that had no slots. What the defect costs is the call:
of the 7 calls whose last lookup came back empty, 0 booked, against 82 of 120
whose last lookup had times.
"""

from __future__ import annotations

from src.agent.prompts import (
    _render_fitting_progress,
    fitting_steps_collected,
    next_fitting_question,
)

from .test_pipeline_fsm_wire import Harness

CLOSED = "2026-09-16"
OPEN = "2026-09-17"


def _progress(h: Harness) -> dict:
    return h.pipeline._build_fitting_progress(None, krok8_confirmed=False)


def _closed_day_block(**session_fields: object) -> str:
    """The block a `60ae3fdd`-shaped session actually produces.

    Built through `_build_fitting_progress` rather than from a hand-written
    dict. Handing the renderer `date: None` directly would assume the very
    thing under test: every assertion about «which step comes next» would then
    hold no matter what the builder did with the closed day.
    """
    h = Harness()
    h.session.fitting_customer_name = "Олена"
    h.session.caller_phone = "0996036709"
    h.session.fitting_storage_choice = "own"
    h.session.fitting_stations_seen = [
        {"id": "000000001", "city": "Дніпро", "address": "м. Дніпро, Донецьке шосе, 69"}
    ]
    h.session.selected_fitting_date = CLOSED
    h.session.fitting_dates_no_slots.add(CLOSED)
    for field, value in session_fields.items():
        setattr(h.session, field, value)
    progress = h.pipeline._build_fitting_progress(
        h.pipeline._resolve_selected_station(), krok8_confirmed=False
    )
    return _render_fitting_progress(progress)


class TestADayWithNoSlotsIsNotACollectedDate:
    def test_a_closed_day_does_not_fill_the_date_row(self) -> None:
        h = Harness()
        h.session.selected_fitting_date = CLOSED
        h.session.fitting_dates_no_slots.add(CLOSED)

        assert _progress(h)["date"] is None

    def test_the_closed_day_is_named_not_dropped(self) -> None:
        """Blanking the row alone would invite the same day to be asked for again."""
        h = Harness()
        h.session.selected_fitting_date = CLOSED
        h.session.fitting_dates_no_slots.add(CLOSED)

        assert _progress(h)["date_no_slots"] == CLOSED

    def test_a_day_that_later_returned_times_is_collected_again(self) -> None:
        """`fitting_dates_no_slots` is station-agnostic and never emptied.

        A first lookup against the wrong station can put a perfectly bookable
        day in the set, so membership alone must not be enough to demote it.
        """
        h = Harness()
        h.session.selected_fitting_date = CLOSED
        h.session.fitting_dates_no_slots.add(CLOSED)
        h.session.fitting_slots_offered = [{"date": CLOSED, "time": "11:40"}]

        progress = _progress(h)
        assert progress["date"] == CLOSED
        assert progress.get("date_no_slots") is None

    def test_slots_for_a_different_day_do_not_reopen_this_one(self) -> None:
        h = Harness()
        h.session.selected_fitting_date = CLOSED
        h.session.fitting_dates_no_slots.add(CLOSED)
        h.session.fitting_slots_offered = [{"date": OPEN, "time": "11:40"}]

        assert _progress(h)["date"] is None

    def test_a_date_the_lookup_never_refused_is_untouched(self) -> None:
        h = Harness()
        h.session.selected_fitting_date = OPEN
        h.session.fitting_dates_no_slots.add(CLOSED)

        progress = _progress(h)
        assert progress["date"] == OPEN
        assert progress.get("date_no_slots") is None

    def test_a_closed_day_supplied_by_the_fsm_is_also_demoted(self) -> None:
        """The check runs after the gap-fill, so both sources of the date pass it."""
        h = Harness()
        h.session.selected_fitting_date = None
        h.session.fsm_filled_fields["date"] = CLOSED
        h.session.fitting_dates_no_slots.add(CLOSED)

        progress = _progress(h)
        assert progress["date"] is None
        assert progress["date_no_slots"] == CLOSED

    def test_a_call_with_no_date_gains_no_closed_marker(self) -> None:
        h = Harness()
        h.session.fitting_dates_no_slots.add(CLOSED)

        progress = _progress(h)
        assert progress["date"] is None
        assert progress.get("date_no_slots") is None


class TestWhatTheBlockTellsTheModelAboutAClosedDay:
    def test_the_date_row_is_pending_and_names_the_day(self) -> None:
        block = _closed_day_block()
        assert f"⏳ Дата: {CLOSED}" in block
        assert f"✅ Дата: {CLOSED}" not in block

    def test_the_row_forbids_naming_an_hour_on_that_day(self) -> None:
        """The exact sentence `60ae3fdd` produced was an hour on a closed day."""
        assert "НЕ називай на ній годину" in _closed_day_block()

    def test_the_block_no_longer_claims_the_slots_were_read_out(self) -> None:
        """«слоти вже озвучено» was false on every one of these calls.

        It rides on the Krok 4 banner, which only renders while the date row is
        filled — so the assertion is on the claim, not on the branch that emits
        it, and stays true however that banner is reworded.
        """
        assert "слоти вже озвучено" not in _closed_day_block()

    def test_the_model_is_not_forbidden_to_look_the_day_up_again(self) -> None:
        """The Krok 4 banner's «НЕ викликай get_fitting_slots з новою датою».

        That line is right when the date holds and fatal when it does not: it
        left the model with a closed day, no slot list, and no way to ask 1С
        for another.
        """
        assert "НЕ викликай get_fitting_slots з новою датою" not in _closed_day_block()

    def test_the_only_allowed_action_is_asking_for_a_date(self) -> None:
        block = _closed_day_block()
        assert "ЄДИНА ДОЗВОЛЕНА ДІЯ ЗАРАЗ: задай Крок 3 (Дата)" in block
        assert "Крок 4 (Час)" not in block

    def test_a_closed_day_outranks_the_remembered_weekday(self) -> None:
        """The weekday row would send the lookup back to the refused day.

        Its whole point is «НЕ ПИТАЙ ще раз, одразу виклич get_fitting_slots» on
        the weekday the caller named — which, when that weekday is the day 1С
        just closed, is a round trip to the same refusal.
        """
        block = _closed_day_block(fitting_requested_weekday=2)
        assert "на цю дату вільних слотів НЕМАЄ" in block
        assert "ЩЕ РАНІШЕ просив" not in block


class TestTheChecklistHelpersAgree:
    def test_the_next_question_asks_for_a_date(self) -> None:
        p = {
            "customer_name": "Олена",
            "city": "Дніпро",
            "storage_choice": "own",
            "date": None,
            "date_no_slots": CLOSED,
        }
        assert fitting_steps_collected(p)["date"] is False
        assert "дату" in next_fitting_question(p)
