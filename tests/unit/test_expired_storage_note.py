"""A booking by an expired storage contract tells the caller about the surcharge.

1C keeps returning a contract after its DateEnd, and the tyres are still on its
stock (0957044150: contract 00000110596, ended 2026-08-23, four tyres). Booking
by it is allowed; the centre bills the days past the term, and the caller should
hear that before arriving.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.core.call_session import CallSession
from src.main import _record_storage_contracts
from tests.unit.test_reschedule_state_pin import (
    STATION,
    _book_args,
    _onec_mock,
    _ready_session,
    _run,
)

CONTRACT = "00000110596"


def _find_storage_answer(date_end: str) -> dict[str, Any]:
    """The shape 1C `findStorage` really returns (captured 2026-09-28)."""
    return {
        "success": True,
        "data": [
            {
                "ID": "000006460",
                "Number": CONTRACT,
                "Date": "2026-07-23T00:00:00",
                "DateEnd": f"{date_end}T00:00:00",
                "StorageWares": [
                    {
                        "StorageWaresID": "000026088",
                        "StorageWaresName": "225/45R17 Taurus Winter /1",
                        "StorageWaresDisk": False,
                        "StorageWaresStock": 1,
                    }
                ],
            }
        ],
        "errors": [],
    }


def _days_from_today(days: int) -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=days)).isoformat()


async def _book_by_contract(session: CallSession, contract: str = CONTRACT) -> dict[str, Any]:
    session.fitting_station_ids = {STATION}
    result = await _run(session, "book_fitting", _book_args(storage_contract=contract), _onec_mock())
    assert result.get("status") == "confirmed", result
    return result


def test_only_a_contract_past_its_end_is_noted() -> None:
    session = CallSession.__new__(CallSession)
    session.storage_contracts_found = []
    session.storage_contracts_expired = {}

    _record_storage_contracts(session, _find_storage_answer("2026-08-23"))

    assert session.storage_contracts_found == [CONTRACT]
    assert session.storage_contracts_expired == {CONTRACT: "2026-08-23"}


def test_a_running_contract_is_not_noted() -> None:
    session = CallSession.__new__(CallSession)
    session.storage_contracts_found = []
    session.storage_contracts_expired = {}

    _record_storage_contracts(session, _find_storage_answer(_days_from_today(30)))

    assert session.storage_contracts_found == [CONTRACT]
    assert session.storage_contracts_expired == {}


def test_the_expired_list_survives_redis() -> None:
    session = _ready_session()
    session.storage_contracts_expired = {CONTRACT: "2026-08-23"}
    restored = CallSession.from_dict(session.to_dict())
    assert restored.storage_contracts_expired == {CONTRACT: "2026-08-23"}


@pytest.mark.asyncio
async def test_booking_by_an_expired_contract_speaks_the_surcharge() -> None:
    session = _ready_session()
    _record_storage_contracts(session, _find_storage_answer("2026-08-23"))

    result = await _book_by_contract(session)

    assert "закінчився — це було двадцять третє серпня" in result["message"]
    assert "доплату розрахують у шинному центрі" in result["message"]


@pytest.mark.asyncio
async def test_booking_by_a_running_contract_says_nothing_extra() -> None:
    session = _ready_session()
    _record_storage_contracts(session, _find_storage_answer(_days_from_today(30)))

    result = await _book_by_contract(session)

    assert "Приїжджайте" in result["message"]
    assert "доплат" not in result["message"]


@pytest.mark.asyncio
async def test_own_tyres_do_not_hear_about_someone_elses_contract() -> None:
    """The contract is on file, the caller brings their own ("NONE")."""
    session = _ready_session()
    _record_storage_contracts(session, _find_storage_answer("2026-08-23"))

    result = await _book_by_contract(session, contract="NONE")

    assert "доплат" not in result["message"]
