"""Wave 18: the Wave 14 date guard blocked a date the FSM had already parsed.

Call `4065c49d` (2026-09-11). STT mangled two spoken «понеділок» into
«нафанетивов» and «на Он идиот»; the caller then said «на 14». `date_parser`
took the bare day (`d54547e`) and the FSM moved DATE→TIME with
`date=2026-09-14`. The LLM correctly called `get_fitting_slots(date_from=
"2026-09-14")` — and the Wave 14 guard refused it, because it decides «did the
caller name a date» from `mentions_date()` over `dialog_history`, and
`mentions_date("на 14")` is `False`. The bot re-asked the date the caller had
just given.

These tests drive the real `get_fitting_slots` handler out of
`_build_tool_router`, not a predicate — the guard is one condition at a call
site, and a test that does not cross that seam proves nothing about it.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.store_client.client import StoreClient

STATION = "000000003"
FSM_DATE = "2026-09-14"

# Verbatim from `4065c49d`, turns 11-15. None of the three is a date to
# `mentions_date` — that is the whole point of the call.
GARBLED_TURNS: tuple[str, ...] = ("нафанетивов", "на Он идиот", "на 14")


class _Schedule:
    """Stands in for the 1C client. `spec=` on the AsyncMock is deliberate:
    a bare mock would make a renamed `get_station_schedule` look green."""

    async def get_station_schedule(
        self, station_id: str, date_from: str, date_to: str
    ) -> dict[str, Any]:
        raise NotImplementedError


def _session(*, fsm_date: str | None, inferred: bool = False) -> CallSession:
    session = CallSession(uuid.uuid4())
    for turn in GARBLED_TURNS:
        session.add_user_turn(turn)
    if fsm_date is not None:
        session.fsm_filled_fields["date"] = fsm_date
        if inferred:
            session.fsm_inferred_fields.append("date")
    return session


async def _call_slots(session: CallSession, date_from: str = FSM_DATE) -> Any:
    onec = _Schedule()
    onec.get_station_schedule = AsyncMock(  # type: ignore[method-assign]
        spec=_Schedule.get_station_schedule, return_value={"data": []}
    )
    with patch("src.main._onec_client", onec), patch("src.main._redis", None):
        router = _build_tool_router(session, store_client=AsyncMock(spec=StoreClient))
        return await router.execute(
            "get_fitting_slots",
            {"station_id": STATION, "date_from": date_from, "date_to": date_from},
        )


def _was_blocked(result: Any) -> bool:
    return isinstance(result, dict) and result.get("reason") == "date_not_asked"


class TestTheCallThatBrokeIt:
    """`4065c49d` — replayed through the registered tool."""

    @pytest.mark.asyncio
    async def test_a_date_the_fsm_parsed_is_not_re_asked(self) -> None:
        session = _session(fsm_date=FSM_DATE)
        assert not _was_blocked(await _call_slots(session))

    @pytest.mark.asyncio
    async def test_the_lookup_actually_runs(self) -> None:
        """Not blocked is not the same as got through — check the side effect."""
        session = _session(fsm_date=FSM_DATE)
        await _call_slots(session)
        assert session.selected_fitting_date == FSM_DATE

    @pytest.mark.asyncio
    async def test_the_guard_is_not_spent(self) -> None:
        """An exempted call must leave the one-shot budget for a real offender."""
        session = _session(fsm_date=FSM_DATE)
        await _call_slots(session)
        assert session.fitting_date_guard_fired is False


class TestTheGuardStillGuards:
    """Wave 14's own case — call `add8354b`, a date nobody ever named."""

    @pytest.mark.asyncio
    async def test_no_fsm_date_still_blocks(self) -> None:
        session = _session(fsm_date=None)
        assert _was_blocked(await _call_slots(session))

    @pytest.mark.asyncio
    async def test_empty_fsm_date_still_blocks(self) -> None:
        """A refusal writes the field; an empty string is not an answer."""
        session = _session(fsm_date="")
        assert _was_blocked(await _call_slots(session))

    @pytest.mark.asyncio
    async def test_an_inferred_date_still_blocks(self) -> None:
        """Only `city` is inferred today. If a wave ever infers a date, this
        guard must not be the thing that silently accepts it."""
        session = _session(fsm_date=FSM_DATE, inferred=True)
        assert _was_blocked(await _call_slots(session))

    @pytest.mark.asyncio
    async def test_blocking_spends_the_one_shot_budget(self) -> None:
        session = _session(fsm_date=None)
        await _call_slots(session)
        assert session.fitting_date_guard_fired is True


class TestTheOldPathsAreUntouched:
    """The two sources the guard already consulted must keep working."""

    @pytest.mark.asyncio
    async def test_a_spoken_month_name_still_exempts(self) -> None:
        session = _session(fsm_date=None)
        session.add_user_turn("на 14 сентября")
        assert not _was_blocked(await _call_slots(session))

    @pytest.mark.asyncio
    async def test_a_weekday_pinned_by_the_pipeline_still_exempts(self) -> None:
        session = _session(fsm_date=None)
        session.fitting_requested_weekday = 0
        assert not _was_blocked(await _call_slots(session))
