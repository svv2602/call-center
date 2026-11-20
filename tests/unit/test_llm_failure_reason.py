"""A failed LLM provider attempt carries a reason, and the reason reaches a person.

Until now a provider failure was a number without a cause
(``callcenter_llm_errors_total``) and the only LLM alert asked for 360
fallbacks an hour — on a handful of calls a day it could not fire. Now:

* every failed attempt gets one reason from the closed list
  ``FAILURE_REASONS``; what is not recognised is ``other``, never an
  exception and never nothing;
* an empty balance and a rate limit — both HTTP 429 at OpenAI — are told apart
  by the reply body, read while it is whole (the exception message keeps 300
  characters of it);
* the router counts each failed attempt once in
  ``callcenter_llm_provider_failures_total{provider, task, reason}`` and
  exports the series at zero on start-up, because ``increase()`` cannot see
  the first failure of a series born with the value 1;
* ``prometheus/alerts.yml`` fires on a single failure and a single switch.

Providers here are real ``AbstractProvider`` subclasses (or the real
``OpenAICompatProvider`` over a fake HTTP session) behind a real ``LLMRouter``
with real circuit breakers; counters are read from the Prometheus registry.
Reply bodies are made up after the providers' documentation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import socket
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
import anthropic
import httpx
import pytest
import yaml
from aiobreaker import CircuitBreaker, CircuitBreakerError
from prometheus_client import REGISTRY

from src.llm.models import LLMResponse, LLMTask, StreamDone, StreamEvent, TextDelta, Usage
from src.llm.providers.base import AbstractProvider
from src.llm.providers.openai_compat import (
    FAILURE_REASONS,
    OpenAICompatProvider,
    ProviderHTTPError,
    classify_http_failure,
)
from src.llm.router import LLMRouter, failure_reason
from src.monitoring.metrics import llm_fallbacks_total, llm_provider_failures_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_REPO = pathlib.Path(__file__).resolve().parents[2]
_MESSAGES = [{"role": "user", "content": "доброго дня"}]
_BOUND_SEC = 3.0
_SHORT_DEADLINE_SEC = 0.05
_API_KEY = "sk-test-not-a-real-key-0123456789"

# --- reply bodies, shaped after the providers' documentation ---------------- #

OPENAI_QUOTA = json.dumps(
    {
        "error": {
            "message": "You exceeded your current quota, please check your plan and billing details.",
            "type": "insufficient_quota",
            "param": None,
            "code": "insufficient_quota",
        }
    }
)
OPENAI_RATE_LIMIT = json.dumps(
    {
        "error": {
            "message": "Rate limit reached for gpt-4.1-mini on requests per min (RPM): Limit 500.",
            "type": "requests",
            "param": None,
            "code": "rate_limit_exceeded",
        }
    }
)
OPENAI_BAD_KEY = json.dumps(
    {
        "error": {
            "message": "Incorrect API key provided.",
            "type": "invalid_request_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }
)
OPENAI_BAD_REQUEST = json.dumps(
    {
        "error": {
            "message": "Invalid schema for function 'search_tires'.",
            "type": "invalid_request_error",
            "param": "tools[0]",
            "code": "invalid_function_parameters",
        }
    }
)
GEMINI_EXHAUSTED = json.dumps(
    [
        {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted.",
                "status": "RESOURCE_EXHAUSTED",
            }
        }
    ]
)
DEEPSEEK_NO_BALANCE = json.dumps(
    {
        "error": {
            "message": "Insufficient Balance",
            "type": "unknown_error",
            "code": "invalid_request_error",
        }
    }
)
ANTHROPIC_BILLING = {
    "type": "error",
    "error": {"type": "billing_error", "message": "Billing issue."},
}
ANTHROPIC_LOW_CREDIT = {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API.",
    },
}
ANTHROPIC_RATE_LIMIT = {
    "type": "error",
    "error": {"type": "rate_limit_error", "message": "This request would exceed your rate limit."},
}
ANTHROPIC_OVERLOADED = {
    "type": "error",
    "error": {"type": "overloaded_error", "message": "Overloaded"},
}

# The quota marker lies past the 300 characters the exception message keeps.
_PADDING = "x" * 400
OPENAI_QUOTA_LONG = json.dumps(
    {"error": {"message": _PADDING, "type": "insufficient_quota", "code": "insufficient_quota"}}
)
assert OPENAI_QUOTA_LONG.index("insufficient_quota") > 300

HTTP_TABLE: list[tuple[int, object, str]] = [
    # quota against rate limit: the same status, different bodies
    (429, OPENAI_QUOTA, "quota"),
    (429, OPENAI_RATE_LIMIT, "rate_limit"),
    (429, OPENAI_QUOTA_LONG, "quota"),
    (429, OPENAI_QUOTA.encode(), "quota"),
    (429, json.loads(OPENAI_QUOTA), "quota"),
    (429, GEMINI_EXHAUSTED, "rate_limit"),
    (429, ANTHROPIC_RATE_LIMIT, "rate_limit"),
    (429, "", "rate_limit"),
    (429, "<html>Too Many Requests</html>", "rate_limit"),
    # a billing marker decides whatever the status is
    (
        400,
        json.dumps(
            {"error": {"code": "billing_hard_limit_reached", "message": "Billing hard limit"}}
        ),
        "quota",
    ),
    (402, DEEPSEEK_NO_BALANCE, "quota"),
    (402, ANTHROPIC_BILLING, "quota"),
    (400, ANTHROPIC_LOW_CREDIT, "quota"),
    (401, OPENAI_BAD_KEY, "auth"),
    (401, "", "auth"),
    (
        403,
        json.dumps({"error": {"code": "unsupported_country_region_territory", "message": "no"}}),
        "auth",
    ),
    (408, "", "timeout"),
    (500, "Internal Server Error", "http_5xx"),
    (502, "<html>Bad Gateway</html>", "http_5xx"),
    (
        503,
        json.dumps({"error": {"message": "The engine is overloaded.", "type": "server_error"}}),
        "http_5xx",
    ),
    (529, ANTHROPIC_OVERLOADED, "http_5xx"),
    (599, "", "http_5xx"),
    # not recognised — still a reason, never an exception
    (400, OPENAI_BAD_REQUEST, "other"),
    (
        404,
        json.dumps({"error": {"message": "The model does not exist", "code": "model_not_found"}}),
        "other",
    ),
    (418, "I'm a teapot", "other"),
    (600, "", "other"),
    (302, "", "other"),
]

# Bodies no documentation describes: none of them may raise.
ODD_BODIES: list[object] = [
    None,
    "",
    "not json at all",
    "{",
    "null",
    "42",
    '"insufficient_quota"',
    "[]",
    "[[]]",
    "[1, 2]",
    '{"error": null}',
    '{"error": "insufficient_quota"}',
    '{"error": {"code": 429, "type": null, "message": null}}',
    '{"error": {"code": ["insufficient_quota"], "message": {"text": "credit balance is too low"}}}',
    b"\xff\xfe\x00 broken bytes",
    {"error": []},
    ["insufficient_quota"],
    12.5,
    object(),
]


def _status_error(cls: type[anthropic.APIStatusError], status: int, body: object) -> Exception:
    request = httpx.Request("POST", "https://llm.invalid/v1/messages")
    return cls("provider said no", response=httpx.Response(status, request=request), body=body)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://llm.invalid/v1/messages")


def _tripped(cause: Exception) -> CircuitBreakerError:
    """The error a breaker raises for the failure that opens it: the cause is chained."""
    try:
        raise CircuitBreakerError(
            "Failures threshold reached, circuit breaker opened.", None
        ) from cause
    except CircuitBreakerError as exc:
        return exc


EXCEPTION_TABLE: list[tuple[str, Exception, str]] = [
    ("asyncio / router deadline", TimeoutError(), "timeout"),
    (
        "aiohttp total timeout",
        aiohttp.ServerTimeoutError("Timeout on reading data from socket"),
        "timeout",
    ),
    (
        "aiohttp connect timeout",
        aiohttp.ConnectionTimeoutError("Connection timeout to host"),
        "timeout",
    ),
    ("anthropic timeout", anthropic.APITimeoutError(request=_request()), "timeout"),
    ("httpx read timeout mid-stream", httpx.ReadTimeout("read timed out"), "timeout"),
    (
        "aiohttp connection error",
        aiohttp.ClientConnectionError("Cannot connect to host"),
        "network",
    ),
    ("aiohttp OS error", aiohttp.ClientOSError(104, "Connection reset by peer"), "network"),
    ("aiohttp server disconnected", aiohttp.ServerDisconnectedError(), "network"),
    (
        "aiohttp broken payload",
        aiohttp.ClientPayloadError("Response payload is not completed"),
        "network",
    ),
    ("DNS failure", socket.gaierror(-3, "Temporary failure in name resolution"), "network"),
    ("connection reset", ConnectionResetError(104, "Connection reset by peer"), "network"),
    ("connection refused", ConnectionRefusedError(111, "Connection refused"), "network"),
    ("anthropic connection error", anthropic.APIConnectionError(request=_request()), "network"),
    (
        "httpx protocol error mid-stream",
        httpx.RemoteProtocolError("peer closed connection"),
        "network",
    ),
    (
        "anthropic 429",
        _status_error(anthropic.RateLimitError, 429, ANTHROPIC_RATE_LIMIT),
        "rate_limit",
    ),
    ("anthropic 401", _status_error(anthropic.AuthenticationError, 401, None), "auth"),
    ("anthropic 403", _status_error(anthropic.PermissionDeniedError, 403, None), "auth"),
    ("anthropic billing", _status_error(anthropic.APIStatusError, 402, ANTHROPIC_BILLING), "quota"),
    (
        "anthropic low credit",
        _status_error(anthropic.BadRequestError, 400, ANTHROPIC_LOW_CREDIT),
        "quota",
    ),
    ("anthropic 500", _status_error(anthropic.InternalServerError, 500, None), "http_5xx"),
    (
        "anthropic 529",
        _status_error(anthropic.APIStatusError, 529, ANTHROPIC_OVERLOADED),
        "http_5xx",
    ),
    (
        "anthropic 400",
        _status_error(anthropic.BadRequestError, 400, {"error": {"type": "invalid_request_error"}}),
        "other",
    ),
    ("http error, quota", ProviderHTTPError("API error 429", status=429, reason="quota"), "quota"),
    (
        "http error, reason off the list",
        ProviderHTTPError("API error 429", status=429, reason="qouta"),
        "other",
    ),
    (
        "breaker opened by a quota failure",
        _tripped(ProviderHTTPError("x", status=429, reason="quota")),
        "quota",
    ),
    ("breaker opened by a timeout", _tripped(TimeoutError()), "timeout"),
    (
        "breaker already open",
        CircuitBreakerError("Timeout not elapsed yet, circuit breaker still open", None),
        "other",
    ),
    ("broken stream chunk", json.JSONDecodeError("Expecting value", "", 0), "other"),
    ("plain runtime error", RuntimeError("connection reset mid-stream"), "other"),
    ("a file error is not the network", FileNotFoundError(2, "No such file or directory"), "other"),
    ("anything else", KeyError("choices"), "other"),
]


# --------------------------------------------------------------------------- #
# Reason of an HTTP reply
# --------------------------------------------------------------------------- #


class TestHttpReason:
    @pytest.mark.parametrize(("status", "body", "reason"), HTTP_TABLE)
    def test_status_and_body_give_the_reason(self, status: int, body: object, reason: str) -> None:
        assert classify_http_failure(status, body) == reason

    def test_an_empty_balance_and_a_rate_limit_differ_on_the_same_429(self) -> None:
        assert classify_http_failure(429, OPENAI_QUOTA) == "quota"
        assert classify_http_failure(429, OPENAI_RATE_LIMIT) == "rate_limit"

    @pytest.mark.parametrize("body", ODD_BODIES, ids=repr)
    @pytest.mark.parametrize("status", [429, 500, 400])
    def test_a_body_nobody_documented_never_raises(self, status: int, body: object) -> None:
        expected = {429: "rate_limit", 500: "http_5xx", 400: "other"}[status]
        assert classify_http_failure(status, body) == expected

    def test_the_tables_cover_the_whole_list(self) -> None:
        from_http = {reason for _, _, reason in HTTP_TABLE}
        from_exceptions = {reason for _, _, reason in EXCEPTION_TABLE}
        assert from_http | from_exceptions == set(FAILURE_REASONS)
        assert set(FAILURE_REASONS) == {
            "timeout", "quota", "rate_limit", "auth", "http_5xx", "network", "other",
        }  # fmt: skip
        assert len(FAILURE_REASONS) == len(set(FAILURE_REASONS))


# --------------------------------------------------------------------------- #
# Reason of an exception
# --------------------------------------------------------------------------- #


class TestExceptionReason:
    @pytest.mark.parametrize(
        ("exc", "reason"),
        [(exc, reason) for _, exc, reason in EXCEPTION_TABLE],
        ids=[name for name, _, _ in EXCEPTION_TABLE],
    )
    def test_exception_gives_the_reason(self, exc: Exception, reason: str) -> None:
        assert failure_reason(exc) == reason

    def test_a_timeout_is_not_filed_under_network(self) -> None:
        # TimeoutError is an OSError and aiohttp's timeout is a connection
        # error: the order of the checks is what keeps them apart.
        exc = aiohttp.ServerTimeoutError("Timeout on reading data from socket")
        assert isinstance(exc, aiohttp.ClientConnectionError)
        assert failure_reason(exc) == "timeout"
        assert failure_reason(TimeoutError()) == "timeout"


# --------------------------------------------------------------------------- #
# The real OpenAI-compatible provider over a fake HTTP session
# --------------------------------------------------------------------------- #


class _Reply:
    """An aiohttp response as the provider uses it: ``async with``, status, text."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _Reply:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _Session:
    """An aiohttp session that answers every POST with the same reply."""

    closed = False

    def __init__(self, status: int, text: str) -> None:
        self._status = status
        self._text = text
        self.posts = 0

    def post(self, url: str, **kwargs: Any) -> _Reply:
        self.posts += 1
        return _Reply(self._status, self._text)

    async def close(self) -> None:
        self.closed = True


def _openai(key: str, status: int, text: str) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(
        api_key=_API_KEY, model="gpt-4.1-mini", base_url="https://llm.invalid/v1", provider_key=key
    )
    provider._session = _Session(status, text)  # type: ignore[assignment]
    return provider


class TestProviderRaisesWithReason:
    @pytest.mark.parametrize(
        ("status", "text", "reason"),
        [
            (429, OPENAI_QUOTA, "quota"),
            (429, OPENAI_RATE_LIMIT, "rate_limit"),
            (401, OPENAI_BAD_KEY, "auth"),
            (503, "Service Unavailable", "http_5xx"),
            (400, OPENAI_BAD_REQUEST, "other"),
        ],
    )
    async def test_blocking_call(self, status: int, text: str, reason: str) -> None:
        provider = _openai("p-blocking", status, text)
        with pytest.raises(ProviderHTTPError) as caught:
            await provider.complete(_MESSAGES)
        assert (caught.value.status, caught.value.reason) == (status, reason)

    @pytest.mark.parametrize(
        ("status", "text", "reason"),
        [
            (429, OPENAI_QUOTA, "quota"),
            (429, OPENAI_RATE_LIMIT, "rate_limit"),
            (401, OPENAI_BAD_KEY, "auth"),
            (503, "Service Unavailable", "http_5xx"),
            (400, OPENAI_BAD_REQUEST, "other"),
        ],
    )
    async def test_stream(self, status: int, text: str, reason: str) -> None:
        provider = _openai("p-stream", status, text)
        with pytest.raises(ProviderHTTPError) as caught:
            async for _ in provider.stream_with_tools(_MESSAGES, []):
                pass
        assert (caught.value.status, caught.value.reason) == (status, reason)

    async def test_the_reason_is_read_from_the_whole_body_blocking(self) -> None:
        provider = _openai("p-long", 429, OPENAI_QUOTA_LONG)
        with pytest.raises(ProviderHTTPError) as caught:
            await provider.complete(_MESSAGES)
        assert caught.value.reason == "quota"
        # ...while the message still keeps only the head of it.
        assert "insufficient_quota" not in str(caught.value)
        assert len(str(caught.value)) < 400

    async def test_the_reason_is_read_from_the_whole_body_stream(self) -> None:
        provider = _openai("p-long", 429, OPENAI_QUOTA_LONG)
        with pytest.raises(ProviderHTTPError) as caught:
            async for _ in provider.stream_with_tools(_MESSAGES, []):
                pass
        assert caught.value.reason == "quota"
        assert "insufficient_quota" not in str(caught.value)
        assert len(str(caught.value)) < 400

    async def test_it_is_still_the_runtime_error_callers_catch(self) -> None:
        provider = _openai("p-compat", 500, "Internal Server Error")
        with pytest.raises(RuntimeError, match="API error 500"):
            await provider.complete(_MESSAGES)


# --------------------------------------------------------------------------- #
# Wiring: the router counts the failure, once, under its reason
# --------------------------------------------------------------------------- #


class Scripted(AbstractProvider):
    """A provider that answers, fails, or never answers."""

    def __init__(
        self,
        text: str = "",
        *,
        fail: Exception | None = None,
        fail_after_first: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self._text = text
        self._fail = fail
        self._fail_after_first = fail_after_first
        self._hang = hang
        self.calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        return LLMResponse(text=self._text, usage=Usage(1, 1))

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
        *,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        return await self.complete(messages, system, max_tokens)

    async def health_check(self) -> bool:
        return True

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 300,
        *,
        tool_choice: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self._hang:
            await asyncio.Event().wait()
        if self._fail is not None:
            raise self._fail
        yield TextDelta(text=self._text)
        if self._fail_after_first is not None:
            raise self._fail_after_first
        yield StreamDone(stop_reason="end_turn", usage=Usage(10, 20))


def _router(providers: dict[str, AbstractProvider]) -> LLMRouter:
    """A router whose agent chain is the given providers, in order."""
    keys = list(providers)
    router = LLMRouter()
    router._providers = dict(providers)
    router._breakers = {
        key: CircuitBreaker(fail_max=5, timeout_duration=timedelta(seconds=30)) for key in keys
    }
    router._config = {
        "providers": {key: {"enabled": True} for key in keys},
        "tasks": {"agent": {"primary": keys[0], "fallbacks": keys[1:]}},
    }
    router._initialized = True
    return router


async def _collect(stream: AsyncIterator[StreamEvent], into: list[StreamEvent]) -> None:
    async for event in stream:
        into.append(event)


async def _stream(router: LLMRouter, **kwargs: Any) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    await asyncio.wait_for(
        _collect(router.complete_stream(LLMTask.AGENT, _MESSAGES, **kwargs), events),
        timeout=_BOUND_SEC,
    )
    return events


def _sample(name: str, labels: dict[str, str]) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def _failures(provider: str, task: str = "agent") -> dict[str, float]:
    """The failure counter of one provider, by reason (a missing series reads 0)."""
    return {
        reason: _sample(
            "callcenter_llm_provider_failures_total",
            {"provider": provider, "task": task, "reason": reason},
        )
        or 0.0
        for reason in FAILURE_REASONS
    }


def _moved(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """What changed between two readings — only the reasons that moved."""
    return {
        reason: after[reason] - before[reason]
        for reason in after
        if after[reason] != before[reason]
    }


def _errors(provider: str) -> float:
    return _sample("callcenter_llm_errors_total", {"provider": provider, "task": "agent"}) or 0.0


def _deadline_hits(provider: str) -> float:
    return (
        _sample(
            "callcenter_llm_first_event_timeouts_total", {"provider": provider, "task": "agent"}
        )
        or 0.0
    )


class TestRouterCountsTheFailure:
    async def test_stream_failure_is_counted_under_its_reason(self) -> None:
        router = _router(
            {
                "w-stream-bad": Scripted(fail=ProviderHTTPError("x", status=429, reason="quota")),
                "w-stream-good": Scripted("Добрий день"),
            }
        )
        bad, good, old = (
            _failures("w-stream-bad"),
            _failures("w-stream-good"),
            _errors("w-stream-bad"),
        )

        events = await _stream(router)

        assert [e.text for e in events if isinstance(e, TextDelta)] == ["Добрий день"]
        assert _moved(bad, _failures("w-stream-bad")) == {"quota": 1.0}
        assert _moved(good, _failures("w-stream-good")) == {}
        # The counter the dashboards stand on keeps counting, as before.
        assert _errors("w-stream-bad") == old + 1

    async def test_blocking_failure_is_counted_under_its_reason(self) -> None:
        router = _router(
            {
                "w-block-bad": Scripted(
                    fail=aiohttp.ClientConnectionError("Cannot connect to host")
                ),
                "w-block-good": Scripted("Добрий день"),
            }
        )
        bad, good, old = _failures("w-block-bad"), _failures("w-block-good"), _errors("w-block-bad")

        response = await router.complete(LLMTask.AGENT, _MESSAGES)

        assert response.text == "Добрий день"
        assert _moved(bad, _failures("w-block-bad")) == {"network": 1.0}
        assert _moved(good, _failures("w-block-good")) == {}
        assert _errors("w-block-bad") == old + 1

    @pytest.mark.parametrize(
        ("status", "text", "reason"),
        [
            (429, OPENAI_QUOTA, "quota"),
            (429, OPENAI_RATE_LIMIT, "rate_limit"),
            (401, OPENAI_BAD_KEY, "auth"),
        ],
    )
    async def test_the_real_provider_reply_reaches_the_counter_in_a_stream(
        self, status: int, text: str, reason: str
    ) -> None:
        key = f"w-real-stream-{reason}"
        router = _router({key: _openai(key, status, text), "w-real-spare": Scripted("ok")})
        before = _failures(key)

        await _stream(router)

        assert _moved(before, _failures(key)) == {reason: 1.0}

    @pytest.mark.parametrize(
        ("status", "text", "reason"),
        [
            (429, OPENAI_QUOTA, "quota"),
            (429, OPENAI_RATE_LIMIT, "rate_limit"),
            (503, "busy", "http_5xx"),
        ],
    )
    async def test_the_real_provider_reply_reaches_the_counter_in_a_blocking_call(
        self, status: int, text: str, reason: str
    ) -> None:
        key = f"w-real-block-{reason}"
        router = _router({key: _openai(key, status, text), "w-real-spare": Scripted("ok")})
        before = _failures(key)

        await router.complete(LLMTask.AGENT, _MESSAGES)

        assert _moved(before, _failures(key)) == {reason: 1.0}

    async def test_a_cancelled_turn_is_not_a_provider_failure(self) -> None:
        """Barge-in cancels the turn mid-request: nobody failed, nobody takes over."""
        silent, spare = Scripted(hang=True), Scripted("ok")
        router = _router({"w-cancelled": silent, "w-cancelled-spare": spare})
        before = _failures("w-cancelled")
        events: list[StreamEvent] = []
        turn = asyncio.ensure_future(
            _collect(router.complete_stream(LLMTask.AGENT, _MESSAGES), events)
        )
        for _ in range(50):  # until the request is in flight
            await asyncio.sleep(0)
            if silent.calls:
                break
        assert silent.calls == 1

        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(turn, timeout=_BOUND_SEC)

        assert _moved(before, _failures("w-cancelled")) == {}
        assert spare.calls == 0
        assert events == []

    async def test_a_missed_first_event_deadline_is_one_timeout(self) -> None:
        router = _router({"w-silent": Scripted(hang=True), "w-silent-spare": Scripted("ok")})
        before, hits = _failures("w-silent"), _deadline_hits("w-silent")

        await _stream(router, first_event_timeout=_SHORT_DEADLINE_SEC)

        # Once here, and once in the deadline's own counter — not twice here.
        assert _moved(before, _failures("w-silent")) == {"timeout": 1.0}
        assert _deadline_hits("w-silent") == hits + 1

    async def test_a_providers_own_timeout_is_a_timeout_but_not_a_missed_deadline(self) -> None:
        router = _router({"w-slow": Scripted(fail=TimeoutError()), "w-slow-spare": Scripted("ok")})
        before, hits = _failures("w-slow"), _deadline_hits("w-slow")

        await _stream(router, first_event_timeout=_BOUND_SEC)

        assert _moved(before, _failures("w-slow")) == {"timeout": 1.0}
        assert _deadline_hits("w-slow") == hits  # the distinction the deadline counter keeps

    async def test_a_failure_after_the_first_event_is_counted_once(self) -> None:
        router = _router(
            {
                "w-midway": Scripted(
                    "Доб", fail_after_first=aiohttp.ClientPayloadError("not completed")
                ),
                "w-midway-spare": Scripted("рий день"),
            }
        )
        before = _failures("w-midway")

        await _stream(router)

        assert _moved(before, _failures("w-midway")) == {"network": 1.0}

    async def test_every_provider_of_a_dead_chain_is_counted(self) -> None:
        router = _router(
            {
                "w-dead-a": Scripted(fail=ProviderHTTPError("x", status=401, reason="auth")),
                "w-dead-b": Scripted(fail=RuntimeError("something new")),
            }
        )
        a, b = _failures("w-dead-a"), _failures("w-dead-b")

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router)

        assert _moved(a, _failures("w-dead-a")) == {"auth": 1.0}
        assert _moved(b, _failures("w-dead-b")) == {"other": 1.0}  # unknown, yet not lost

    async def test_the_failure_that_opens_the_breaker_keeps_its_reason(self) -> None:
        router = _router(
            {
                "w-breaker": Scripted(
                    fail=socket.gaierror(-3, "Temporary failure in name resolution")
                )
            }
        )
        before = _failures("w-breaker")

        for _ in range(6):
            with pytest.raises(RuntimeError, match="All providers failed streaming"):
                await _stream(router)

        # Five reached the provider — the fifth arrives wrapped by the breaker
        # and is still a network failure. The sixth was turned away unasked.
        assert router._providers["w-breaker"].calls == 5  # type: ignore[attr-defined]
        assert _moved(before, _failures("w-breaker")) == {"network": 5.0, "other": 1.0}

    async def test_a_success_counts_nothing(self) -> None:
        router = _router({"w-fine": Scripted("ok")})
        before = _failures("w-fine")
        await _stream(router)
        await router.complete(LLMTask.AGENT, _MESSAGES)
        assert _moved(before, _failures("w-fine")) == {}

    async def test_a_counter_that_cannot_count_is_loud_but_does_not_break_the_call(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        class _Unusable:
            def labels(self, **labels: str) -> None:
                raise ValueError("Incorrect label names")

        monkeypatch.setattr("src.monitoring.metrics.llm_provider_failures_total", _Unusable())
        router = _router(
            {"w-uncounted": Scripted(fail=TimeoutError()), "w-uncounted-spare": Scripted("ok")}
        )
        with caplog.at_level(logging.ERROR, logger="src.llm.router"):
            events = await _stream(router)

        # The caller still gets the spare's reply; the lost count is an ERROR
        # with a traceback, not a silent pass.
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["ok"]
        (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert "was not counted" in record.getMessage()
        assert "w-uncounted" in record.getMessage()
        assert record.exc_info is not None


class TestFailureLog:
    async def test_the_warning_names_provider_task_reason_and_status(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        router = _router(
            {"log-quota": _openai("log-quota", 429, OPENAI_QUOTA), "log-spare": Scripted("ok")}
        )
        with caplog.at_level(logging.WARNING, logger="src.llm.router"):
            await _stream(router)

        lines = [r.getMessage() for r in caplog.records if " failed for task " in r.getMessage()]
        assert len(lines) == 1
        assert lines[0].startswith(
            "Stream provider log-quota failed for task agent: reason=quota, status=429: "
        )

    async def test_no_key_and_no_whole_body_in_the_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tail = "TAIL-OF-A-LONG-REPLY"
        body = json.dumps({"error": {"message": "y" * 2000 + tail, "code": "rate_limit_exceeded"}})
        router = _router(
            {"log-long": _openai("log-long", 429, body), "log-long-spare": Scripted("ok")}
        )
        with caplog.at_level(logging.DEBUG):
            await _stream(router)
            await router.complete(LLMTask.AGENT, _MESSAGES)

        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "reason=rate_limit" in text
        assert tail not in text
        assert _API_KEY not in text
        assert "Bearer" not in text
        assert all(len(r.getMessage()) < 600 for r in caplog.records if r.name == "src.llm.router")

    async def test_a_long_exception_text_is_cut(self, caplog: pytest.LogCaptureFixture) -> None:
        router = _router(
            {"log-wordy": Scripted(fail=ValueError("z" * 5000)), "log-wordy-spare": Scripted("ok")}
        )
        with caplog.at_level(logging.WARNING, logger="src.llm.router"):
            await router.complete(LLMTask.AGENT, _MESSAGES)

        (line,) = [r.getMessage() for r in caplog.records if " failed for task " in r.getMessage()]
        assert line.startswith(
            "Provider log-wordy failed for task agent: reason=other, status=None: zzz"
        )
        assert len(line) < 600


# --------------------------------------------------------------------------- #
# Series exist at zero before the first failure
# --------------------------------------------------------------------------- #


class TestSeriesExportedAtZero:
    async def test_initialize_exports_every_reason_of_every_task_at_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        key = "zero-probe"
        config = {
            "providers": {
                key: {
                    "type": "openai",
                    "model": "gpt-4.1-mini",
                    "base_url": "https://llm.invalid/v1",
                    "api_key_env": "ZERO_PROBE_API_KEY",
                    "enabled": True,
                },
                "zero-probe-second": {
                    "type": "openai",
                    "model": "gemini-2.5-flash",
                    "base_url": "https://llm.invalid/v1",
                    "api_key_env": "ZERO_PROBE_API_KEY",
                    "enabled": True,
                },
                "zero-probe-no-key": {
                    "type": "openai",
                    "model": "gpt-4.1-mini",
                    "base_url": "https://llm.invalid/v1",
                    "api_key_env": "ZERO_PROBE_MISSING_KEY",
                    "enabled": True,
                },
            },
            "tasks": {"agent": {"primary": key, "fallbacks": []}},
        }

        async def _config(redis: Any = None) -> dict[str, Any]:
            return config

        monkeypatch.setenv("ZERO_PROBE_API_KEY", _API_KEY)
        monkeypatch.delenv("ZERO_PROBE_MISSING_KEY", raising=False)
        monkeypatch.setattr(LLMRouter, "_load_config", staticmethod(_config))
        name = "callcenter_llm_provider_failures_total"
        assert _sample(name, {"provider": key, "task": "agent", "reason": "quota"}) is None

        router = LLMRouter()
        await router.initialize()
        try:
            # Every provider that came up, not the first one only: the spare is
            # the one whose first failure matters most.
            for provider in (key, "zero-probe-second"):
                for task in LLMTask:
                    for reason in FAILURE_REASONS:
                        labels = {"provider": provider, "task": task.value, "reason": reason}
                        assert _sample(name, labels) == 0.0, labels
            # A provider that did not come up has nothing to fail with.
            assert (
                _sample(name, {"provider": "zero-probe-no-key", "task": "agent", "reason": "quota"})
                is None
            )
        finally:
            await router.close()

    def test_the_counter_has_the_labels_the_rules_select_on(self) -> None:
        assert llm_provider_failures_total._labelnames == ("provider", "task", "reason")


# --------------------------------------------------------------------------- #
# prometheus/alerts.yml
# --------------------------------------------------------------------------- #

_FAILURES = "callcenter_llm_provider_failures_total"
_FALLBACKS = "callcenter_llm_fallbacks_total"
_LLM_RULES = ("LLMProviderFailure", "LLMProviderQuotaOrAuth", "LLMFallbackActivated")


def _rules() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load((_REPO / "prometheus" / "alerts.yml").read_text())
    return {rule["alert"]: rule for group in config["groups"] for rule in group["rules"]}


def _flat(expr: str) -> str:
    return re.sub(r"\s+", " ", expr).strip()


class TestAlertRules:
    def test_the_unreachable_rate_rule_is_replaced_not_kept(self) -> None:
        rules = _rules()
        assert "LLMFallbackRateHigh" not in rules
        assert set(_LLM_RULES) <= set(rules)
        for name, rule in rules.items():
            # Nothing left that waits for a per-second rate of LLM events.
            if "callcenter_llm_fallbacks_total" in rule["expr"] or _FAILURES in rule["expr"]:
                assert "rate(" not in rule["expr"], name

    @pytest.mark.parametrize("name", _LLM_RULES)
    def test_one_event_fires_at_once(self, name: str) -> None:
        rule = _rules()[name]
        expr = _flat(rule["expr"])
        assert "[15m]" in expr
        assert expr.endswith("> 0")
        assert "for" not in rule  # the event is over by the next evaluation

    def test_a_failure_on_the_agent_task_fires_with_its_reason(self) -> None:
        rule = _rules()["LLMProviderFailure"]
        expr = _flat(rule["expr"])
        assert f'increase({_FAILURES}{{task="agent", reason!~"quota|auth"}}[15m])' in expr
        assert "sum by (provider, reason)" in expr
        assert rule["labels"]["severity"] == "high"

    def test_quota_and_auth_are_the_highest_severity_on_any_task(self) -> None:
        rule = _rules()["LLMProviderQuotaOrAuth"]
        expr = _flat(rule["expr"])
        assert f'increase({_FAILURES}{{reason=~"quota|auth"}}[15m])' in expr
        assert "task=" not in expr
        assert "sum by (provider, reason)" in expr
        assert rule["labels"]["severity"] == "critical"

    def test_the_two_failure_rules_split_the_reasons_without_a_gap(self) -> None:
        # Every reason of the closed list lands in exactly one of the rules.
        rules = _rules()
        (excluded,) = re.findall(r'reason!~"([^"]+)"', rules["LLMProviderFailure"]["expr"])
        (included,) = re.findall(r'reason=~"([^"]+)"', rules["LLMProviderQuotaOrAuth"]["expr"])
        assert excluded == included
        named = set(included.split("|"))
        assert named == {"quota", "auth"}
        assert named < set(FAILURE_REASONS)

    def test_a_single_switch_fires_even_for_a_series_born_at_one(self) -> None:
        rule = _rules()["LLMFallbackActivated"]
        expr = _flat(rule["expr"])
        by = "sum by (from_provider, to_provider, task)"
        assert f"{by} ( increase({_FALLBACKS}[15m]) ) > 0" in expr
        assert f"or {by} ( {_FALLBACKS} unless {_FALLBACKS} offset 15m ) > 0" in expr
        assert set(llm_fallbacks_total._labelnames) == {"from_provider", "to_provider", "task"}

    @pytest.mark.parametrize("name", _LLM_RULES)
    def test_the_text_a_person_reads_names_the_cause(self, name: str) -> None:
        # The Telegram template prints the description only, as HTML.
        rule = _rules()[name]
        description = rule["annotations"]["description"]
        assert rule["annotations"]["summary"]
        assert not set("<>&") & set(description), description
        if name == "LLMFallbackActivated":
            for label in ("from_provider", "to_provider", "task"):
                assert f"{{{{ $labels.{label} }}}}" in description
        else:
            assert "{{ $labels.provider }}" in description
            assert "{{ $labels.reason }}" in description

    @pytest.mark.parametrize("name", _LLM_RULES)
    def test_templates_use_only_labels_the_expression_keeps(self, name: str) -> None:
        rule = _rules()[name]
        kept = set(re.findall(r"sum by \(([^)]+)\)", rule["expr"])[0].replace(" ", "").split(","))
        for text in rule["annotations"].values():
            assert set(re.findall(r"\$labels\.(\w+)", text)) <= kept, text

    @pytest.mark.parametrize("name", _LLM_RULES)
    def test_expression_brackets_are_balanced(self, name: str) -> None:
        # No promtool on the build machine: the least a typo check can do.
        expr = _rules()[name]["expr"]
        for opening, closing in ("()", "{}", "[]"):
            assert expr.count(opening) == expr.count(closing), (name, opening)
        assert expr.count('"') % 2 == 0

    def test_severities_are_ones_alertmanager_routes(self) -> None:
        tmpl = yaml.safe_load((_REPO / "alertmanager" / "config.yml.tmpl").read_text())
        assert tmpl["route"]["receiver"] == "telegram"
        routed = {route["match"]["severity"] for route in tmpl["route"]["routes"]}
        assert {"critical", "high"} <= routed
        assert all(route["receiver"] == "telegram" for route in tmpl["route"]["routes"])
        rules = _rules()
        assert rules["LLMProviderQuotaOrAuth"]["labels"]["severity"] in routed
        assert rules["LLMProviderFailure"]["labels"]["severity"] in routed
