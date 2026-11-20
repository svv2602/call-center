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

Providers here are real ``AbstractProvider`` subclasses behind a real
``LLMRouter`` with real circuit breakers.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

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

    async def test_the_task_chain_without_an_override_is_left_as_it_was(self) -> None:
        # Not a wish, a pin: the plain primary → fallbacks chain has always
        # switched mid-stream, and this change is not allowed to move it —
        # neither with the live-call keyword nor without.
        for live in ({}, {"override_fallback": True}):
            primary = ScriptedProvider("Добрий день, слухаю", fail_after_first=True)
            fallback = ScriptedProvider("Добрий день!")
            router = _router({SPARE: primary, PINNED: fallback})

            events = await _stream(router, **live)

            assert _texts(events) == ["Добрий день, слухаю", "Добрий день!"]


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

        response = await router.complete(
            LLMTask.AGENT, _MESSAGES, provider_override=PINNED, override_fallback=True
        )

        assert response.text == "Добрий день!"

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
        assert marked == {"src/main.py": [("StreamingAgentLoop", "live_call")]}

        by_hand = {
            str(path.relative_to(_REPO))
            for path in _python_sources()
            if _calls_with_keyword(path, {"override_fallback", "first_event_timeout"})
        }
        assert by_hand == {"src/llm/router.py"}

    def test_only_the_voice_loop_reads_the_live_call_options(self) -> None:
        readers = {
            str(path.relative_to(_REPO))
            for path in _python_sources()
            if path.name != "router.py"
            and "live_call_stream_options" in path.read_text(encoding="utf-8")
        }
        assert readers == {"src/agent/streaming_loop.py"}
