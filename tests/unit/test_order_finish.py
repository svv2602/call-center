"""An order ends as a request for a manager, never as a confirmed order.

Owner decision 2026-09-28: the bot creates a *request* in 1C, a manager calls
back and confirms it. So after ``confirm_order`` the caller hears «заявку
прийнято, менеджер зателефонує», never «замовлення підтверджено»; the
technical number stays out of the LLM-visible result; when both 1C and the
Store API fallback fail the caller hears «передам менеджеру», never
«прийнято», and no raw exception reaches the ToolRouter.

The handler under test is a closure inside ``_build_tool_router``, so every
test drives the **registered** handler through the real router — a copy of
the logic here would only test the copy.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

import pytest

from src.agent.prompts import (
    _MOD_OBJECTIONS,
    _MOD_ORDER_FLOW,
    _STAGE_ORDER_CONFIRMATION,
    ORDER_REQUEST_CREATED_TEXT,
    ORDER_REQUEST_FAILED_TEXT,
    assemble_prompt,
)
from src.agent.tool_result_compressor import compress_tool_result
from src.agent.tools import ALL_TOOLS
from src.core.call_session import CallSession
from src.main import _build_tool_router
from src.onec_client.client import OneCClient
from src.sandbox.mock_tools import MOCK_RESPONSES, build_mock_tool_router
from src.store_client.client import StoreClient

# «замовлення … підтверджено/оформлено» in either word order, ua + ru.
CONFIRMED_CLAIM = re.compile(
    r"підтверджено|оформлено|подтвержд[её]н|оформлен\b|замовлення\s+(?:\w+\s+){0,2}підтверджен",
    re.IGNORECASE,
)
RESERVE_PROMISE = re.compile(r"зарезерв|на добу|на 24 год|отложу|відкладу", re.IGNORECASE)
FREE_DELIVERY = re.compile(r"безкоштовн|бесплатн", re.IGNORECASE)

ORDER_SCENARIOS = ("tire_search", "consultation")


def _draft() -> dict[str, Any]:
    return {
        "items": [{"product_id": "sku-under-test", "quantity": 2}],
        "customer_phone": "+380000000000",
        "delivery_type": "pickup",
        "city": "Київ",
        "address": "",
        "pickup_point_id": "pp-under-test",
    }


def _onec(create: Any) -> AsyncMock:
    """A 1C client that only answers methods `OneCClient` really has."""
    onec = create_autospec(OneCClient, instance=True)
    if isinstance(create, BaseException):
        onec.create_order_1c.side_effect = create
    else:
        onec.create_order_1c.return_value = create
    return onec


def _store(confirm: Any) -> Any:
    store = create_autospec(StoreClient, instance=True)
    if isinstance(confirm, BaseException):
        store.confirm_order.side_effect = confirm
    else:
        store.confirm_order.return_value = confirm
    return store


async def _confirm(session: CallSession, onec: Any, store: Any, **args: Any) -> Any:
    args = {"order_id": "DRAFT-x", "payment_method": "cod", **args}
    with (
        patch("src.main._onec_client", onec),
        patch("src.main._call_logger", None),
        patch("src.main._redis", None),
    ):
        router = _build_tool_router(session, store_client=store)
        return await router.execute("confirm_order", args)


def _session_with_draft() -> CallSession:
    session = CallSession(uuid.uuid4())
    session.order_draft = _draft()
    return session


# ── _confirm_order: the success path ──────────────────────────────────────


class TestRequestCreatedIn1C:
    @pytest.mark.asyncio
    async def test_result_is_a_request_not_a_confirmed_order(self) -> None:
        session = _session_with_draft()
        onec = _onec({"success": True, "number": "onec-internal-number"})
        result = await _confirm(session, onec, _store(RuntimeError("unused")))

        onec.create_order_1c.assert_awaited_once()
        assert result["status"] == "request_created"
        assert result["status"] != "confirmed"
        assert not CONFIRMED_CLAIM.search(result["message"])
        assert "менеджер" in result["message"]

    @pytest.mark.asyncio
    async def test_no_number_the_bot_could_read_out(self) -> None:
        session = _session_with_draft()
        onec = _onec({"success": True, "number": "onec-internal-number"})
        result = await _confirm(session, onec, _store(RuntimeError("unused")))

        # The number is generated inside the handler — read it back from the
        # session, not from the fixture.
        assert session.order_id, "request number must still be kept in the session"
        # The number is in the raw result for the audit row only
        # (`_audit_order_number`); what the model reads never names it.
        dumped = compress_tool_result("confirm_order", result)
        assert session.order_id not in dumped
        assert "onec-internal-number" not in json.dumps(result, ensure_ascii=False)
        assert session.order_draft is None


# ── _confirm_order: the failure path ──────────────────────────────────────


class TestRequestFailed:
    @pytest.mark.asyncio
    async def test_1c_and_store_api_both_raise(self) -> None:
        session = _session_with_draft()
        result = await _confirm(
            session,
            _onec(RuntimeError("1C down")),
            _store(RuntimeError("Store API down")),
        )
        assert isinstance(result, dict)
        assert result.get("status") == "request_failed"
        assert "прийнято" not in result["message"].lower()
        assert not CONFIRMED_CLAIM.search(result["message"])
        assert "Передам менеджеру" in result["message"]
        # Not the ToolRouter's generic {"error": "<exception text>"}.
        assert "down" not in json.dumps(result, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_store_api_error_dict_is_a_failure(self) -> None:
        session = _session_with_draft()
        result = await _confirm(
            session,
            _onec(RuntimeError("1C down")),
            _store({"error": 'Store API 404: {"detail":"Not Found"}'}),
        )
        assert result.get("status") == "request_failed"

    @pytest.mark.asyncio
    async def test_echoed_order_id_alone_is_no_success_marker(self) -> None:
        """StoreClient.confirm_order echoes the requested id when the API
        answers without one — that echo must not read as a created request."""
        session = CallSession(uuid.uuid4())  # no draft → straight to Store API
        result = await _confirm(
            session,
            _onec(RuntimeError("unused")),
            _store({"order_id": "DRAFT-x", "order_number": None, "status": None}),
        )
        assert result.get("status") == "request_failed"

    @pytest.mark.asyncio
    async def test_store_api_fallback_success_is_a_request(self) -> None:
        session = _session_with_draft()
        store = _store({"order_id": "x", "order_number": "store-number", "status": "new"})
        result = await _confirm(session, _onec(RuntimeError("1C down")), store)
        store.confirm_order.assert_awaited_once()
        assert result.get("status") == "request_created"
        assert "store-number" not in compress_tool_result("confirm_order", result)

    @pytest.mark.asyncio
    async def test_failure_marks_error_so_the_turn_does_not_retry(self) -> None:
        session = _session_with_draft()
        result = await _confirm(
            session, _onec(RuntimeError("1C down")), _store(RuntimeError("down"))
        )
        # streaming_loop blocks an identical re-call only on `error is True`.
        assert result.get("error") is True


# ── Texts the LLM sees ────────────────────────────────────────────────────


class TestResultTexts:
    def test_created_text_says_request_and_manager(self) -> None:
        assert "Заявку прийнято" in ORDER_REQUEST_CREATED_TEXT
        assert "менеджер" in ORDER_REQUEST_CREATED_TEXT
        assert not CONFIRMED_CLAIM.search(ORDER_REQUEST_CREATED_TEXT)

    def test_failed_text_never_says_accepted(self) -> None:
        assert "прийнято" not in ORDER_REQUEST_FAILED_TEXT.lower()
        assert not CONFIRMED_CLAIM.search(ORDER_REQUEST_FAILED_TEXT)
        assert "Передам менеджеру" in ORDER_REQUEST_FAILED_TEXT

    def test_no_literal_numbers_in_result_texts(self) -> None:
        for text in (ORDER_REQUEST_CREATED_TEXT, ORDER_REQUEST_FAILED_TEXT):
            assert not re.search(r"\d", text)


class TestAssembledOrderPrompt:
    @pytest.mark.parametrize("scenario", ORDER_SCENARIOS)
    def test_no_confirmed_order_claim(self, scenario: str) -> None:
        prompt = assemble_prompt(scenario)
        assert not re.search(r"\bпідтверджено\b|\bоформлено\b", prompt, re.IGNORECASE)
        assert not re.search(r"замовлення\s+(?:\w+\s+){0,2}підтверджен", prompt, re.IGNORECASE)

    @pytest.mark.parametrize("scenario", ORDER_SCENARIOS)
    def test_no_reserve_promise(self, scenario: str) -> None:
        # The fitting module forbids «Зарезервувала слот» — that is a slot,
        # not goods; the order modules themselves must carry no reserve.
        for module in (_MOD_ORDER_FLOW, _MOD_OBJECTIONS):
            assert module in assemble_prompt(scenario)
            assert not RESERVE_PROMISE.search(module)

    @pytest.mark.parametrize("scenario", ORDER_SCENARIOS)
    def test_no_hardcoded_free_delivery(self, scenario: str) -> None:
        assert not FREE_DELIVERY.search(assemble_prompt(scenario))

    def test_quantity_is_not_forced_to_a_set_of_four(self) -> None:
        assert "комплект із чотирьох" not in _MOD_ORDER_FLOW
        assert "за шину" in _MOD_ORDER_FLOW

    def test_payment_methods_come_from_network_conditions(self) -> None:
        assert "онлайн або карткою" not in _MOD_ORDER_FLOW
        assert "Умови мережі" in _MOD_ORDER_FLOW
        assert "Умови мережі" in _MOD_OBJECTIONS

    def test_order_flow_tells_both_outcomes(self) -> None:
        assert "request_failed" in _MOD_ORDER_FLOW
        assert "Передам менеджеру" in _MOD_ORDER_FLOW
        assert "Заявку прийнято" in _MOD_ORDER_FLOW

    def test_confirmation_stage_asks_to_pass_a_request(self) -> None:
        assert "підтверджуєте замовлення" not in _STAGE_ORDER_CONFIRMATION
        assert "заявк" in _STAGE_ORDER_CONFIRMATION.lower()


class TestToolDescription:
    def test_confirm_order_is_described_as_a_request(self) -> None:
        tool = next(t for t in ALL_TOOLS if t["name"] == "confirm_order")
        desc = tool["description"].lower()
        assert "заявк" in desc
        assert "фіналізувати" not in desc
        # The 1C payment codes exist only for these three; installments and
        # prepay are the manager's job (no invented codes).
        assert set(tool["input_schema"]["properties"]["payment_method"]["enum"]) == {
            "cod",
            "online",
            "card_on_delivery",
        }


class TestSandboxMock:
    def test_mock_matches_the_live_result_shape(self) -> None:
        mock = MOCK_RESPONSES["confirm_order"]
        assert mock["status"] == "request_created"
        assert mock["message"] == ORDER_REQUEST_CREATED_TEXT
        assert not CONFIRMED_CLAIM.search(json.dumps(mock, ensure_ascii=False))

    @pytest.mark.asyncio
    async def test_mock_router_answers_with_a_request(self) -> None:
        result = await build_mock_tool_router().execute(
            "confirm_order", {"order_id": "x", "payment_method": "cod"}
        )
        assert result["status"] == "request_created"
        assert not CONFIRMED_CLAIM.search(json.dumps(result, ensure_ascii=False))
