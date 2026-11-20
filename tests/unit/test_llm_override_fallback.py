"""A live call pinned to one LLM leaves it for the agent chain when it fails.

A network with ``tenants.config.agent_provider_override`` used to get a chain
of exactly that provider: when it failed or went silent, the caller got an
error turn although a second provider was configured for the ``agent`` task.
Behind FF_LLM_OVERRIDE_FALLBACK the voice loop of a real call now asks the
router to treat the override as the first choice, and (optionally) to wait
for each provider's first stream event only FF_LLM_FIRST_EVENT_TIMEOUT_SEC.

What must not move:

* sandbox, goldset and replays name a model and get that model or an error —
  a reply from another model makes a run incomparable, and a deadline would
  cut the time-to-first-event a replay is there to measure;
* nothing is spoken twice — the router switches only while it has yielded
  nothing to the caller;
* with the flag off the call to the router is the one it always was.

The blocking path of a live call (``LLMAgent``: the farewell on every call,
every turn while FF_STREAMING_LLM is off) runs on the network's model as well,
flag or no flag, and under the flag gets the same chain behind it. Under the
flag a network *without* a pinned model does not switch mid-reply either. A
switch is counted when the spare yields its first event, so a turn the caller
barged into is counted too.

Providers here are real ``AbstractProvider`` subclasses behind a real
``LLMRouter`` with real circuit breakers.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from aiobreaker import CircuitBreaker
from prometheus_client import REGISTRY

from src.agent.agent import LLMAgent, ToolRouter
from src.agent.prompts import ERROR_TEXT
from src.agent.streaming_loop import StreamingAgentLoop
from src.config import FeatureFlagSettings
from src.llm.models import LLMResponse, LLMTask, StreamDone, StreamEvent, TextDelta, Usage
from src.llm.providers.base import AbstractProvider
from src.llm.router import LLMRouter, live_call_stream_options
from tests.unit.mocks.mock_audio_socket import MockAudioSocketConnection
from tests.unit.mocks.mock_tts import MockTTSEngine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_REPO = pathlib.Path(__file__).resolve().parents[2]

# The production shape: the network is pinned to the provider that is also the
# agent task's fallback, so the chain behind the pin is the task's primary.
PINNED = "openai-gpt41-mini"
SPARE = "gemini-2.5-flash"

_FLAG = "FF_LLM_OVERRIDE_FALLBACK"
_DEADLINE = "FF_LLM_FIRST_EVENT_TIMEOUT_SEC"
_MESSAGES = [{"role": "user", "content": "доброго дня"}]

#: Every await on a provider that may hang is bounded by this, far below the
#: suite's ``--timeout``: a missing deadline fails the test, it does not stall it.
_BOUND_SEC = 3.0
_SHORT_DEADLINE_SEC = 0.05


class ScriptedProvider(AbstractProvider):
    """A provider that answers, fails, or never answers — and counts its calls."""

    def __init__(
        self,
        text: str = "",
        *,
        fail: Exception | None = None,
        fail_after_first: bool = False,
        hang: bool = False,
    ) -> None:
        self._text = text
        self._fail = fail
        self._fail_after_first = fail_after_first
        self._hang = hang
        self.stream_calls = 0
        self.streams_finished = 0
        self.complete_calls = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.complete_calls += 1
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
        self.stream_calls += 1
        try:
            if self._hang:
                await asyncio.Event().wait()
            if self._fail is not None:
                raise self._fail
            yield TextDelta(text=self._text)
            if self._fail_after_first:
                raise RuntimeError("connection reset mid-stream")
            yield StreamDone(stop_reason="end_turn", usage=Usage(10, 20))
        finally:
            self.streams_finished += 1


def _router(
    providers: dict[str, ScriptedProvider],
    *,
    primary: str = SPARE,
    fallbacks: tuple[str, ...] = (PINNED,),
) -> LLMRouter:
    router = LLMRouter()
    router._providers = dict(providers)
    router._breakers = {
        key: CircuitBreaker(fail_max=5, timeout_duration=timedelta(seconds=30)) for key in providers
    }
    router._config = {
        "providers": {key: {"enabled": True} for key in providers},
        "tasks": {"agent": {"primary": primary, "fallbacks": list(fallbacks)}},
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


def _texts(events: list[StreamEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, TextDelta)]


def _fallbacks(from_key: str, to_key: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "callcenter_llm_fallbacks_total",
            {"from_provider": from_key, "to_provider": to_key, "task": "agent"},
        )
        or 0.0
    )


def _all_fallbacks() -> float:
    """Every switch counted so far, whatever its labels."""
    return sum(
        sample.value
        for metric in REGISTRY.collect()
        for sample in metric.samples
        if sample.name == "callcenter_llm_fallbacks_total"
    )


def _deadline_hits(provider: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "callcenter_llm_first_event_timeouts_total", {"provider": provider, "task": "agent"}
        )
        or 0.0
    )


@pytest.fixture(autouse=True)
def _flags_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.delenv(_DEADLINE, raising=False)


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #


class TestChain:
    def test_an_override_alone_is_the_whole_chain(self) -> None:
        router = _router({PINNED: ScriptedProvider(), SPARE: ScriptedProvider()})
        assert router._resolve_chain(LLMTask.AGENT, PINNED) == [PINNED]

    def test_a_live_call_gets_the_task_chain_behind_the_override(self) -> None:
        router = _router(
            {"pin": ScriptedProvider(), "a": ScriptedProvider(), "b": ScriptedProvider()},
            primary="a",
            fallbacks=("b",),
        )
        chain = router._resolve_chain(LLMTask.AGENT, "pin", override_fallback=True)
        assert chain == ["pin", "a", "b"]

    def test_the_override_is_not_repeated_when_the_task_chain_names_it(self) -> None:
        # Production: pinned to the provider that is also the task's fallback.
        router = _router({PINNED: ScriptedProvider(), SPARE: ScriptedProvider()})
        chain = router._resolve_chain(LLMTask.AGENT, PINNED, override_fallback=True)
        assert chain == [PINNED, SPARE]

    def test_a_provider_that_is_not_up_stays_out(self) -> None:
        router = _router({PINNED: ScriptedProvider()}, primary="no-key-in-env")
        chain = router._resolve_chain(LLMTask.AGENT, PINNED, override_fallback=True)
        assert chain == [PINNED]

    def test_an_unknown_override_is_still_an_error(self) -> None:
        router = _router({SPARE: ScriptedProvider()})
        with pytest.raises(RuntimeError, match="not found"):
            router._resolve_chain(LLMTask.AGENT, "missing", override_fallback=True)

    def test_without_an_override_the_task_chain_is_what_it_was(self) -> None:
        router = _router({PINNED: ScriptedProvider(), SPARE: ScriptedProvider()})
        assert router._resolve_chain(LLMTask.AGENT, None) == [SPARE, PINNED]
        assert router._resolve_chain(LLMTask.AGENT, None, override_fallback=True) == [SPARE, PINNED]


# --------------------------------------------------------------------------- #
# Failure before the first event
# --------------------------------------------------------------------------- #


class TestFailureBeforeFirstEvent:
    async def test_live_call_is_answered_by_the_next_provider(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        before = _fallbacks(PINNED, SPARE)

        events = await _stream(router, provider_override=PINNED, override_fallback=True)

        assert _texts(events) == ["Добрий день!"]
        done = events[-1]
        assert isinstance(done, StreamDone) and done.provider_key == SPARE
        assert (pinned.stream_calls, spare.stream_calls) == (1, 1)
        assert _fallbacks(PINNED, SPARE) == before + 1

    async def test_a_named_model_gets_no_other_model(self) -> None:
        # Sandbox, goldset, replay: the override without the live-call keyword.
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        before = _fallbacks(PINNED, SPARE)

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router, provider_override=PINNED)

        assert spare.stream_calls == 0
        assert _fallbacks(PINNED, SPARE) == before

    async def test_the_pinned_provider_is_asked_once_when_everything_is_down(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider(fail=RuntimeError("also down"))
        router = _router({PINNED: pinned, SPARE: spare})

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router, provider_override=PINNED, override_fallback=True)

        assert (pinned.stream_calls, spare.stream_calls) == (1, 1)

    async def test_an_open_breaker_on_the_pinned_provider_leads_to_the_next_one(self) -> None:
        pinned = ScriptedProvider("never asked")
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        router._breakers[PINNED].open()

        events = await _stream(router, provider_override=PINNED, override_fallback=True)

        assert _texts(events) == ["Добрий день!"]
        assert pinned.stream_calls == 0

    async def test_an_open_breaker_fails_a_named_model(self) -> None:
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: ScriptedProvider("never asked"), SPARE: spare})
        router._breakers[PINNED].open()

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router, provider_override=PINNED)

        assert spare.stream_calls == 0


# --------------------------------------------------------------------------- #
# Failure after the first event
# --------------------------------------------------------------------------- #


class TestFailureAfterFirstEvent:
    async def test_a_live_call_does_not_start_the_reply_over(self) -> None:
        pinned = ScriptedProvider("Добрий день, слухаю", fail_after_first=True)
        spare = ScriptedProvider("Добрий день! Чим можу допомогти?")
        router = _router({PINNED: pinned, SPARE: spare})
        heard: list[StreamEvent] = []

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await asyncio.wait_for(
                _collect(
                    router.complete_stream(
                        LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
                    ),
                    heard,
                ),
                timeout=_BOUND_SEC,
            )

        assert _texts(heard) == ["Добрий день, слухаю"]
        assert spare.stream_calls == 0

    async def test_the_task_chain_without_the_flag_is_left_as_it_was(self) -> None:
        # Not a wish, a pin: the plain primary → fallbacks chain has always
        # switched mid-stream, and without the live-call keyword (flag off,
        # or not a live call) it still does.
        primary = ScriptedProvider("Добрий день, слухаю", fail_after_first=True)
        fallback = ScriptedProvider("Добрий день!")
        router = _router({SPARE: primary, PINNED: fallback})

        events = await _stream(router)

        assert _texts(events) == ["Добрий день, слухаю", "Добрий день!"]
        assert fallback.stream_calls == 1

    async def test_a_live_call_without_a_pinned_model_does_not_start_over_either(self) -> None:
        # Flag on, the network has no override: the keyword alone is what
        # stops the switch. The spare is in the chain and up — it answers the
        # same request when the failure comes before the first event (the
        # next test).
        primary = ScriptedProvider("Добрий день, слухаю", fail_after_first=True)
        fallback = ScriptedProvider("Добрий день!")
        router = _router({SPARE: primary, PINNED: fallback})
        assert router._resolve_chain(LLMTask.AGENT, None, override_fallback=True) == [SPARE, PINNED]
        heard: list[StreamEvent] = []

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await asyncio.wait_for(
                _collect(
                    router.complete_stream(LLMTask.AGENT, _MESSAGES, override_fallback=True), heard
                ),
                timeout=_BOUND_SEC,
            )

        assert _texts(heard) == ["Добрий день, слухаю"]
        assert (primary.stream_calls, fallback.stream_calls) == (1, 0)

    async def test_a_live_call_without_a_pinned_model_still_switches_before_the_first_event(
        self,
    ) -> None:
        # The other half of the pair above: same router, same keyword, the
        # failure one step earlier — so the silence there is the rule's doing,
        # not a spare that could not have answered.
        primary = ScriptedProvider(fail=RuntimeError("503"))
        fallback = ScriptedProvider("Добрий день!")
        router = _router({SPARE: primary, PINNED: fallback})

        events = await _stream(router, override_fallback=True)

        assert _texts(events) == ["Добрий день!"]
        assert (primary.stream_calls, fallback.stream_calls) == (1, 1)


# --------------------------------------------------------------------------- #
# When a switch is counted
# --------------------------------------------------------------------------- #


class TestSwitchIsCountedAtTheFirstEventOfTheSpare:
    async def test_a_reply_the_caller_barged_into_is_counted(self) -> None:
        # Barge-in: the consumer takes the spare's first event and closes the
        # stream. The switch happened — the caller heard the spare.
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        before, before_all = _fallbacks(PINNED, SPARE), _all_fallbacks()

        stream = router.complete_stream(
            LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
        )
        first = await asyncio.wait_for(stream.__anext__(), timeout=_BOUND_SEC)
        await stream.aclose()

        assert isinstance(first, TextDelta) and first.text == "Добрий день!"
        assert _fallbacks(PINNED, SPARE) == before + 1
        assert _all_fallbacks() == before_all + 1

    async def test_a_reply_read_to_the_end_is_counted_once(self) -> None:
        # Two events come from the spare (text, done): one switch, not two.
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        before, before_all = _fallbacks(PINNED, SPARE), _all_fallbacks()

        events = await _stream(router, provider_override=PINNED, override_fallback=True)

        assert len(events) == 2
        assert _fallbacks(PINNED, SPARE) == before + 1
        assert _all_fallbacks() == before_all + 1

    async def test_a_turn_cancelled_before_the_spare_answers_is_not_a_switch(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider(hang=True)
        router = _router({PINNED: pinned, SPARE: spare})
        before = _all_fallbacks()
        events: list[StreamEvent] = []

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                _collect(
                    router.complete_stream(
                        LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
                    ),
                    events,
                ),
                timeout=0.3,
            )

        assert events == []
        assert spare.stream_calls == 1
        assert _all_fallbacks() == before

    async def test_the_first_provider_answering_is_not_a_switch(self) -> None:
        # Under any labels: a slip here would count the provider to itself.
        pinned = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("spare")})
        before = _all_fallbacks()

        events = await _stream(router, provider_override=PINNED, override_fallback=True)

        assert _texts(events) == ["Добрий день!"]
        assert _all_fallbacks() == before

    async def test_a_spare_that_fails_too_is_not_a_switch(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider(fail=RuntimeError("also down"))
        router = _router({PINNED: pinned, SPARE: spare})
        before = _all_fallbacks()

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router, provider_override=PINNED, override_fallback=True)

        assert _all_fallbacks() == before

    async def test_a_mid_reply_switch_of_the_plain_chain_is_counted_once(self) -> None:
        # Flag off, no override: the old mid-stream switch, counted at the
        # fallback's first event like any other.
        primary = ScriptedProvider("Добрий день, слухаю", fail_after_first=True)
        fallback = ScriptedProvider("Добрий день!")
        router = _router({SPARE: primary, PINNED: fallback})
        before, before_all = _fallbacks(SPARE, PINNED), _all_fallbacks()

        await _stream(router)

        assert _fallbacks(SPARE, PINNED) == before + 1
        assert _all_fallbacks() == before_all + 1


# --------------------------------------------------------------------------- #
# The first-event deadline
# --------------------------------------------------------------------------- #


class TestFirstEventDeadline:
    async def test_a_silent_provider_is_left_for_the_next_one(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        pinned = ScriptedProvider(hang=True)
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        hits, switched = _deadline_hits(PINNED), _fallbacks(PINNED, SPARE)

        with caplog.at_level(logging.WARNING, logger="src.llm.router"):
            events = await _stream(
                router,
                provider_override=PINNED,
                override_fallback=True,
                first_event_timeout=_SHORT_DEADLINE_SEC,
            )

        assert _texts(events) == ["Добрий день!"]
        assert _deadline_hits(PINNED) == hits + 1
        assert _fallbacks(PINNED, SPARE) == switched + 1
        missed = [r for r in caplog.records if "first event deadline" in r.getMessage()]
        assert len(missed) == 1
        assert missed[0].levelno == logging.WARNING
        assert PINNED in missed[0].getMessage() and "agent" in missed[0].getMessage()

    async def test_the_abandoned_stream_is_closed_and_counts_against_the_breaker(self) -> None:
        pinned = ScriptedProvider(hang=True)
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("Добрий день!")})

        await _stream(
            router,
            provider_override=PINNED,
            override_fallback=True,
            first_event_timeout=_SHORT_DEADLINE_SEC,
        )

        # The provider's generator ran its `finally` (the HTTP response is
        # released), and the breaker saw a failure, not a clean call.
        assert pinned.streams_finished == 1
        assert router._breakers[PINNED].fail_counter == 1

    async def test_five_missed_deadlines_open_the_breaker(self) -> None:
        pinned = ScriptedProvider(hang=True)
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("Добрий день!")})

        for _ in range(6):
            events = await _stream(
                router,
                provider_override=PINNED,
                override_fallback=True,
                first_event_timeout=_SHORT_DEADLINE_SEC,
            )
            assert _texts(events) == ["Добрий день!"]

        # The sixth call never reached the silent provider.
        assert pinned.stream_calls == 5

    async def test_without_a_deadline_the_provider_is_waited_for(self) -> None:
        pinned = ScriptedProvider(hang=True)
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        events: list[StreamEvent] = []

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                _collect(
                    router.complete_stream(
                        LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
                    ),
                    events,
                ),
                timeout=0.3,
            )

        assert events == []
        assert spare.stream_calls == 0

    async def test_the_last_provider_has_a_deadline_too(self) -> None:
        pinned, spare = ScriptedProvider(hang=True), ScriptedProvider(hang=True)
        router = _router({PINNED: pinned, SPARE: spare})
        hits = _deadline_hits(SPARE)

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(
                router,
                provider_override=PINNED,
                override_fallback=True,
                first_event_timeout=_SHORT_DEADLINE_SEC,
            )

        assert _deadline_hits(SPARE) == hits + 1

    async def test_the_deadline_also_bounds_a_named_model_if_asked(self) -> None:
        # The deadline is its own keyword: nobody but a live call passes it,
        # but it does not need the fallback keyword to work.
        router = _router({PINNED: ScriptedProvider(hang=True)})

        with pytest.raises(RuntimeError, match="All providers failed streaming"):
            await _stream(router, provider_override=PINNED, first_event_timeout=_SHORT_DEADLINE_SEC)

    async def test_a_provider_timeout_inside_the_deadline_is_not_a_deadline_hit(self) -> None:
        pinned = ScriptedProvider(fail=TimeoutError("aiohttp total timeout"))
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("Добрий день!")})
        hits = _deadline_hits(PINNED)

        events = await _stream(
            router, provider_override=PINNED, override_fallback=True, first_event_timeout=30.0
        )

        assert _texts(events) == ["Добрий день!"]
        assert _deadline_hits(PINNED) == hits

    async def test_a_prompt_provider_is_not_touched_by_the_deadline(self) -> None:
        pinned = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("spare")})
        hits = _deadline_hits(PINNED)

        events = await _stream(
            router, provider_override=PINNED, override_fallback=True, first_event_timeout=30.0
        )

        assert _texts(events) == ["Добрий день!"]
        assert _deadline_hits(PINNED) == hits


# --------------------------------------------------------------------------- #
# The blocking API
# --------------------------------------------------------------------------- #


class TestBlockingComplete:
    async def test_the_keyword_gives_complete_the_same_chain(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})

        before = _fallbacks(PINNED, SPARE)

        response = await router.complete(
            LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
        )

        assert response.text == "Добрий день!"
        assert _fallbacks(PINNED, SPARE) == before + 1

    async def test_a_named_model_fails_alone(self) -> None:
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: ScriptedProvider(fail=RuntimeError("503")), SPARE: spare})

        with pytest.raises(RuntimeError, match="All providers failed"):
            await router.complete(LLMTask.AGENT, _MESSAGES, provider_override=PINNED)

        assert spare.complete_calls == 0


# --------------------------------------------------------------------------- #
# The flag
# --------------------------------------------------------------------------- #


class TestFlag:
    def test_both_settings_are_off_by_default(self) -> None:
        flags = FeatureFlagSettings()
        assert flags.llm_override_fallback is False
        assert flags.llm_first_event_timeout_sec == 0.0

    def test_no_flag_no_options(self) -> None:
        assert live_call_stream_options() == {}

    def test_the_flag_alone_asks_for_the_chain_and_no_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        assert live_call_stream_options() == {"override_fallback": True}

    def test_the_deadline_comes_with_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        assert live_call_stream_options() == {
            "override_fallback": True,
            "first_event_timeout": 12.5,
        }

    def test_the_deadline_without_the_flag_is_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Rollback is removing one variable: the flag.
        monkeypatch.setenv(_DEADLINE, "12.5")
        assert live_call_stream_options() == {}

    @pytest.mark.parametrize("value", ["0", "-3"])
    def test_a_non_positive_deadline_is_no_deadline(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, value)
        assert live_call_stream_options() == {"override_fallback": True}


# --------------------------------------------------------------------------- #
# The wiring: who asks the router for the live-call behaviour
# --------------------------------------------------------------------------- #


class RecordingRouter:
    """Stands in for the router: keeps the keywords of each ``complete_stream``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_stream(
        self, task: LLMTask, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(kwargs)
        yield TextDelta(text="Добрий день!")
        yield StreamDone(stop_reason="end_turn", usage=Usage(10, 20))


def _loop(router: Any, **kwargs: Any) -> StreamingAgentLoop:
    return StreamingAgentLoop(
        llm_router=router,
        tool_router=ToolRouter(),
        tts=MockTTSEngine(),  # type: ignore[arg-type]
        conn=MockAudioSocketConnection(),  # type: ignore[arg-type]
        barge_in_event=asyncio.Event(),
        system_prompt="Test system prompt",
        provider_override=PINNED,
        **kwargs,
    )


class TestVoiceLoopWiring:
    async def test_flag_on_the_live_loop_asks_for_the_chain_and_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        router = RecordingRouter()

        await _loop(router, live_call=True).run_turn("доброго дня", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert call["override_fallback"] is True
        assert call["first_event_timeout"] == 12.5

    async def test_flag_off_the_live_loop_calls_the_router_as_before(self) -> None:
        router = RecordingRouter()

        await _loop(router, live_call=True).run_turn("доброго дня", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert "override_fallback" not in call
        assert "first_event_timeout" not in call

    async def test_a_loop_nobody_called_live_ignores_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        router = RecordingRouter()

        await _loop(router).run_turn("доброго дня", [])

        (call,) = router.calls
        assert "override_fallback" not in call
        assert "first_event_timeout" not in call

    async def test_flag_on_a_live_turn_is_spoken_by_the_next_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The loop against the real router: the keywords it sends are the
        # keywords `complete_stream` takes.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, str(_SHORT_DEADLINE_SEC))
        pinned = ScriptedProvider(hang=True)
        router = _router({PINNED: pinned, SPARE: ScriptedProvider("Добрий день!")})

        result = await asyncio.wait_for(
            _loop(router, live_call=True).run_turn("доброго дня", []), timeout=_BOUND_SEC
        )

        assert result.spoken_text == "Добрий день!"
        assert result.provider_key == SPARE
        assert pinned.stream_calls == 1

    async def test_flag_off_a_live_turn_with_a_failed_pinned_provider_is_an_error(self) -> None:
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: ScriptedProvider(fail=RuntimeError("503")), SPARE: spare})

        result = await _loop(router, live_call=True).run_turn("доброго дня", [])

        assert result.stop_reason == "error"
        assert result.spoken_text == ""
        assert spare.stream_calls == 0


class TestSandboxStaysOnItsModel:
    async def test_a_sandbox_agent_fails_on_its_model_with_the_flag_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `create_sandbox_agent` (goldset included) builds exactly this:
        # an LLMAgent with the router and a provider override.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: ScriptedProvider(fail=RuntimeError("503")), SPARE: spare})
        agent = LLMAgent(
            api_key="k",
            system_prompt="base",
            llm_router=router,
            tool_router=ToolRouter(),
            provider_override=PINNED,
        )

        text, _history = await agent.process_message("доброго дня", [])

        assert text == ERROR_TEXT
        assert spare.complete_calls == 0


def _calls_with_keyword(path: pathlib.Path, keywords: set[str]) -> list[tuple[str, str]]:
    """``(callee, keyword)`` of every call in ``path`` that passes one of ``keywords``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, "id", None) or getattr(node.func, "attr", "?")
        found.extend((callee, k.arg) for k in node.keywords if k.arg in keywords)
    return found


def _python_sources() -> list[pathlib.Path]:
    return sorted([*(_REPO / "src").rglob("*.py"), *(_REPO / "scripts").rglob("*.py")])


class TestWhoIsALiveCall:
    def test_main_marks_its_voice_loop_as_a_live_call(self) -> None:
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))
        loops = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "StreamingAgentLoop"
        ]
        assert loops
        for call in loops:
            value = next((k.value for k in call.keywords if k.arg == "live_call"), None)
            assert isinstance(value, ast.Constant) and value.value is True

    def test_nobody_else_is_a_live_call(self) -> None:
        # Sandbox, goldset, replays, background tasks: none of them marks a
        # loop live, and none passes the router's live-call keywords by hand.
        marked = {
            str(path.relative_to(_REPO)): hits
            for path in _python_sources()
            if (hits := _calls_with_keyword(path, {"live_call"}))
        }
        assert {path: sorted(hits) for path, hits in marked.items()} == {
            "src/main.py": [("LLMAgent", "live_call"), ("StreamingAgentLoop", "live_call")]
        }

        by_hand = {
            str(path.relative_to(_REPO))
            for path in _python_sources()
            if _calls_with_keyword(path, {"override_fallback", "first_event_timeout"})
        }
        assert by_hand == {"src/llm/router.py"}

    def test_only_the_voice_loop_and_the_agent_read_the_live_call_options(self) -> None:
        # Both read them behind their own `live_call` parameter, which only
        # `main.py` sets (the test above).
        readers = {
            str(path.relative_to(_REPO))
            for path in _python_sources()
            if path.name != "router.py"
            and "live_call_stream_options" in path.read_text(encoding="utf-8")
        }
        assert readers == {"src/agent/agent.py", "src/agent/streaming_loop.py"}

    def test_main_marks_its_agent_as_a_live_call_on_the_networks_model(self) -> None:
        # The source half of `TestMainBuildsTheAgentOfALiveCall`: one agent is
        # built, it is marked live by a literal, and it is handed the same
        # variable the voice loop is.
        tree = ast.parse((_REPO / "src/main.py").read_text(encoding="utf-8"))

        def calls(name: str) -> list[ast.Call]:
            return [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
            ]

        def keyword(call: ast.Call, name: str) -> ast.expr | None:
            return next((k.value for k in call.keywords if k.arg == name), None)

        (agent,) = calls("LLMAgent")
        (loop,) = calls("StreamingAgentLoop")
        live = keyword(agent, "live_call")
        assert isinstance(live, ast.Constant) and live.value is True
        agent_override = keyword(agent, "provider_override")
        loop_override = keyword(loop, "provider_override")
        assert isinstance(agent_override, ast.Name) and isinstance(loop_override, ast.Name)
        assert agent_override.id == loop_override.id

    def test_the_sandbox_agent_is_not_a_live_call(self) -> None:
        tree = ast.parse((_REPO / "src/sandbox/agent_runner.py").read_text(encoding="utf-8"))
        agents = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "LLMAgent"
        ]
        assert agents
        for call in agents:
            assert "live_call" not in {k.arg for k in call.keywords}
            assert "provider_override" in {k.arg for k in call.keywords}


# --------------------------------------------------------------------------- #
# The blocking path of a live call: LLMAgent
# --------------------------------------------------------------------------- #


class RecordingCompleteRouter:
    """Stands in for the router: keeps the keywords of each ``complete``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, task: LLMTask, messages: list[dict[str, Any]], **kwargs: Any
    ) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(text="Добрий день!", usage=Usage(1, 1))


def _agent(router: Any, **kwargs: Any) -> LLMAgent:
    return LLMAgent(
        api_key="k",
        system_prompt="base",
        llm_router=router,
        tool_router=ToolRouter(),
        **kwargs,
    )


class TestBlockingAgentWiring:
    async def test_flag_off_the_live_agent_adds_only_the_networks_model(self) -> None:
        router = RecordingCompleteRouter()

        await _agent(router, provider_override=PINNED, live_call=True).process_message(
            "доброго дня", []
        )

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert "override_fallback" not in call
        assert "first_event_timeout" not in call

    async def test_flag_on_the_live_agent_asks_for_the_chain_and_no_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The deadline is a stream's: `complete()` does not take the keyword.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        router = RecordingCompleteRouter()

        await _agent(router, provider_override=PINNED, live_call=True).process_message(
            "доброго дня", []
        )

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert call["override_fallback"] is True
        assert "first_event_timeout" not in call

    async def test_an_agent_nobody_called_live_ignores_the_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sandbox and goldset: a named model, no live-call mark.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        router = RecordingCompleteRouter()

        await _agent(router, provider_override=PINNED).process_message("доброго дня", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert "override_fallback" not in call
        assert "first_event_timeout" not in call

    async def test_a_live_agent_of_a_network_without_a_model_calls_as_before(self) -> None:
        router = RecordingCompleteRouter()

        await _agent(router, live_call=True).process_message("доброго дня", [])

        (call,) = router.calls
        assert call["provider_override"] is None
        assert "override_fallback" not in call

    async def test_the_summary_of_an_exhausted_turn_goes_the_same_way(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The second `complete` of the blocking path: asked when the tool
        # rounds run out, spoken to the same caller.
        monkeypatch.setenv(_FLAG, "true")
        live, named = RecordingCompleteRouter(), RecordingCompleteRouter()

        await _agent(live, provider_override=PINNED, live_call=True)._request_summary_fallback(
            "system", []
        )
        await _agent(named, provider_override=PINNED)._request_summary_fallback("system", [])

        (live_call,) = live.calls
        assert live_call["provider_override"] == PINNED
        assert live_call["override_fallback"] is True
        (named_call,) = named.calls
        assert named_call["provider_override"] == PINNED
        assert "override_fallback" not in named_call


class TestVoiceLoopSummaryWiring:
    """The voice loop's own blocking `complete`: the summary of an exhausted turn."""

    async def test_flag_on_the_summary_takes_the_networks_model_and_the_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        router = RecordingCompleteRouter()

        await _loop(router, live_call=True)._request_summary_fallback("system", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert call["override_fallback"] is True
        # `complete` has no first event: the deadline must not reach it.
        assert "first_event_timeout" not in call

    async def test_flag_off_the_summary_takes_the_networks_model_alone(self) -> None:
        router = RecordingCompleteRouter()

        await _loop(router, live_call=True)._request_summary_fallback("system", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert "override_fallback" not in call

    async def test_a_loop_nobody_called_live_keeps_its_one_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_FLAG, "true")
        router = RecordingCompleteRouter()

        await _loop(router)._request_summary_fallback("system", [])

        (call,) = router.calls
        assert call["provider_override"] == PINNED
        assert "override_fallback" not in call

    async def test_the_summary_against_the_real_router_leaves_a_failed_pinned_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The keywords the loop sends are the keywords `complete` takes.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Знайшла два варіанти.")
        router = _router({PINNED: pinned, SPARE: spare})

        text = await _loop(router, live_call=True)._request_summary_fallback("system", [])

        assert text == "Знайшла два варіанти."
        assert (pinned.complete_calls, spare.complete_calls) == (1, 1)


class TestBlockingAgentAgainstTheRealRouter:
    async def test_flag_on_a_live_turn_is_answered_by_the_next_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The agent against the real router: the keywords it sends are the
        # keywords `complete` takes.
        monkeypatch.setenv(_FLAG, "true")
        monkeypatch.setenv(_DEADLINE, "12.5")
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})
        before = _fallbacks(PINNED, SPARE)

        text, _history = await _agent(
            router, provider_override=PINNED, live_call=True
        ).process_message("доброго дня", [])

        assert text == "Добрий день!"
        assert (pinned.complete_calls, spare.complete_calls) == (1, 1)
        assert _fallbacks(PINNED, SPARE) == before + 1

    async def test_flag_off_a_live_turn_with_a_failed_pinned_provider_is_an_error(self) -> None:
        pinned = ScriptedProvider(fail=RuntimeError("503"))
        spare = ScriptedProvider("Добрий день!")
        router = _router({PINNED: pinned, SPARE: spare})

        text, _history = await _agent(
            router, provider_override=PINNED, live_call=True
        ).process_message("доброго дня", [])

        assert text == ERROR_TEXT
        assert (pinned.complete_calls, spare.complete_calls) == (1, 0)

    async def test_a_live_turn_is_answered_by_the_networks_model_not_the_task_primary(
        self,
    ) -> None:
        # No flag: the network's model answers although the agent task's
        # primary is another provider and is up.
        pinned = ScriptedProvider("Це модель мережі")
        task_primary = ScriptedProvider("Це основна модель задачі")
        router = _router({PINNED: pinned, SPARE: task_primary})
        assert router._resolve_chain(LLMTask.AGENT, None)[0] == SPARE

        text, _history = await _agent(
            router, provider_override=PINNED, live_call=True
        ).process_message("доброго дня", [])

        assert text == "Це модель мережі"
        assert (pinned.complete_calls, task_primary.complete_calls) == (1, 0)

    async def test_a_network_without_a_model_is_served_by_the_task_chain_as_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for flag in (None, "true"):
            if flag:
                monkeypatch.setenv(_FLAG, flag)
            task_primary = ScriptedProvider(fail=RuntimeError("503"))
            fallback = ScriptedProvider("Добрий день!")
            router = _router({SPARE: task_primary, PINNED: fallback})

            text, _history = await _agent(router, live_call=True).process_message("доброго дня", [])

            assert text == "Добрий день!"
            assert (task_primary.complete_calls, fallback.complete_calls) == (1, 1)


# --------------------------------------------------------------------------- #
# The wiring from main.py: handle_call builds the agent of a live call
# --------------------------------------------------------------------------- #


class _FakePipeline:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self) -> None:
        return None


class _FakeSTT:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


async def _no_event(*args: Any, **kwargs: Any) -> None:
    return None


async def _agent_kwargs_of_a_call(tenant_config: dict[str, Any]) -> dict[str, Any]:
    """Run ``handle_call`` for a network with ``tenant_config``; the agent's kwargs."""
    import src.main as main

    tenant = {
        "id": uuid.uuid4(),
        "slug": "net",
        "name": "Net",
        "network_id": "net",
        "agent_name": "Олена",
        "greeting": None,
        "enabled_tools": [],
        "prompt_suffix": None,
        "config": tenant_config,
        "is_active": True,
    }
    built: list[dict[str, Any]] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    async def _tenant(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return tenant

    conn = MagicMock(spec=["channel_uuid", "is_closed"])
    conn.channel_uuid = uuid.uuid4()
    conn.is_closed = False

    with (
        patch.object(main, "_db_engine", None),
        patch.object(main, "_redis", None),
        patch.object(main, "_llm_router", None),
        patch.object(main, "_tts_engine", MagicMock(spec=[])),
        patch.object(main, "_store_client", MagicMock(spec=[])),
        patch.object(main, "_resolve_tenant", _tenant),
        # Both ask Asterisk over HTTP; this call has neither.
        patch.object(main, "_resolve_ivr_intent", _no_event),
        patch.object(main, "_resolve_caller_id", _no_event),
        patch.object(main, "GoogleSTTEngine", _FakeSTT),
        patch.object(main, "LLMAgent", _FakeAgent),
        patch.object(main, "CallPipeline", _FakePipeline),
        patch.object(
            main, "_build_tool_router", lambda *a, **k: MagicMock(spec=["set_execute_hook"])
        ),
        patch.object(main, "publish_event", _no_event),
    ):
        await main.handle_call(conn)

    (kwargs,) = built
    return kwargs


class TestMainBuildsTheAgentOfALiveCall:
    async def test_the_agent_gets_the_networks_model_and_the_live_mark(self) -> None:
        kwargs = await _agent_kwargs_of_a_call({"agent_provider_override": PINNED})

        assert kwargs["provider_override"] == PINNED
        assert kwargs["live_call"] is True

    async def test_a_network_without_a_model_gets_none(self) -> None:
        kwargs = await _agent_kwargs_of_a_call({})

        assert kwargs["provider_override"] is None
        assert kwargs["live_call"] is True
