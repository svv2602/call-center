"""One STT response = one interim hypothesis (call 78139ffb, 2026-10-27).

Google sends the not-yet-settled audio as several consecutive interim
results; the engine used to queue each as its own transcript, and the
pipeline promoted «девушка» alone out of «Алло» + «девушка».
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.stt.base import STTConfig
from src.stt.google_stt import GoogleSTTEngine


def _result(text: str | None, *, final: bool, lang: str = "ru-ru") -> SimpleNamespace:
    alts = [] if text is None else [SimpleNamespace(transcript=text, confidence=0.7)]
    return SimpleNamespace(alternatives=alts, is_final=final, language_code=lang)


async def _run(responses: list[list[SimpleNamespace]]) -> list:
    engine = GoogleSTTEngine()
    engine._config = STTConfig()
    engine._running = True
    engine._transcript_queue = asyncio.Queue()
    engine._audio_queue = asyncio.Queue()
    await engine._audio_queue.put(None)

    async def _responses():
        for results in responses:
            yield SimpleNamespace(results=results)

    async def _streaming_recognize(requests):
        return _responses()

    engine._client = MagicMock()
    engine._client.streaming_recognize = _streaming_recognize
    await engine._run_streaming("recognizer", MagicMock())
    out = []
    while not engine._transcript_queue.empty():
        out.append(engine._transcript_queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_interim_results_of_one_response_are_one_hypothesis() -> None:
    out = await _run([[_result("Алло", final=False), _result("девушка", final=False)]])
    assert [(t.text, t.is_final, t.language) for t in out] == [("Алло девушка", False, "ru-ru")]


@pytest.mark.asyncio
async def test_final_then_interims_in_one_response() -> None:
    out = await _run([[_result("235 55 18", final=True), _result("зимние", final=False)]])
    assert [(t.text, t.is_final) for t in out] == [("235 55 18", True), ("зимние", False)]


@pytest.mark.asyncio
async def test_final_without_alternatives_queues_nothing() -> None:
    assert await _run([[_result(None, final=True)]]) == []
