"""Caller speech STT never finalized (call f9cca234, 2026-10-27).

Three utterances showed up only as interims; the caller heard 16 s of
silence, then «Ви ще на лінії?» and, after a tyre list ending «Яка
підходить?», a «скажіть так або ні» re-prompt.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.pipeline import CallPipeline, _is_pending_confirmation
from src.stt.base import Transcript
from tests.unit.test_barge_in import _FakeConn, _FakeSTT


def _pipeline(transcripts: list[Transcript] | None = None) -> CallPipeline:
    session = MagicMock()
    session.channel_uuid = "test-uuid"
    return CallPipeline(
        conn=_FakeConn(),
        stt=_FakeSTT(transcripts or []),
        tts=MagicMock(),
        agent=MagicMock(),
        session=session,
    )


def _interim(text: str) -> Transcript:
    return Transcript(text=text, is_final=False, confidence=0.0)


def _final(text: str) -> Transcript:
    return Transcript(text=text, is_final=True, confidence=0.9)


@pytest.fixture
def fast_orphan():
    with (
        patch("src.core.pipeline._ORPHAN_INTERIM_SEC", 0.05),
        patch("src.core.pipeline._ORPHAN_POLL_SEC", 0.01),
    ):
        yield


class TestReaderLoopTracksInterims:
    @pytest.mark.asyncio
    async def test_listening_interim_is_pending(self) -> None:
        p = _pipeline([_interim("зимние"), _interim("зимние без")])
        await p._transcript_reader_loop()
        assert p._pending_interim is not None
        assert p._pending_interim[0] == "зимние без"

    @pytest.mark.asyncio
    async def test_final_clears_pending(self) -> None:
        p = _pipeline([_interim("зимние без"), _final("зимние без шипов")])
        await p._transcript_reader_loop()
        assert p._pending_interim is None

    @pytest.mark.asyncio
    async def test_interim_while_speaking_is_not_pending(self) -> None:
        # Barge-in (or echo of the bot's own voice) — not a turn to promote.
        p = _pipeline([_interim("шины что")])
        p._speaking = True
        await p._transcript_reader_loop()
        assert p._pending_interim is None


@pytest.mark.usefixtures("fast_orphan")
class TestOrphanPromotion:
    @pytest.mark.asyncio
    async def test_interim_without_final_becomes_the_turn(self) -> None:
        p = _pipeline()
        p._pending_interim = ("зимние без", asyncio.get_running_loop().time())
        with patch("src.core.pipeline.stt_orphan_interim_total") as metric:
            t = await asyncio.wait_for(p._wait_for_final_transcript(), timeout=2)
        assert t is not None and t.is_final and t.text == "зимние без"
        metric.labels.assert_called_once_with(event="promoted", language="")
        assert p._pending_interim is None

    @pytest.mark.asyncio
    async def test_final_in_time_wins_over_the_interim(self) -> None:
        p = _pipeline()
        p._pending_interim = ("зимние без", asyncio.get_running_loop().time())
        await p._final_transcript_queue.put(_final("зимние без шипов"))
        t = await p._wait_for_final_transcript()
        assert t is not None and t.text == "зимние без шипов"
        assert p._promoted_interim is None

    @pytest.mark.asyncio
    async def test_caller_speech_pushes_the_silence_timeout(self) -> None:
        # Silence timeout 0.1 s, orphan window 0.3 s: an interim heard at the
        # start must not let the wait end in silence (None) at 0.1 s.
        p = _pipeline()
        with (
            patch("src.core.pipeline.SILENCE_TIMEOUT_SEC", 0.1),
            patch("src.core.pipeline._ORPHAN_INTERIM_SEC", 0.3),
        ):
            p._pending_interim = ("а о", asyncio.get_running_loop().time())
            t = await asyncio.wait_for(p._wait_for_final_transcript(), timeout=2)
        assert t is not None and t.text == "а о"

    @pytest.mark.asyncio
    async def test_no_speech_still_times_out(self) -> None:
        p = _pipeline()
        with patch("src.core.pipeline.SILENCE_TIMEOUT_SEC", 0.05):
            assert await p._wait_for_final_transcript() is None


class TestLateFinal:
    @pytest.mark.asyncio
    async def test_late_final_of_promoted_text_is_dropped(self) -> None:
        p = _pipeline([_final("Зимние, без.")])
        p._promoted_interim = ("зимние без", asyncio.get_running_loop().time())
        await p._transcript_reader_loop()
        assert p._final_transcript_queue.get_nowait() is None  # only the end sentinel

    @pytest.mark.asyncio
    async def test_late_final_with_more_words_goes_through(self) -> None:
        p = _pipeline([_final("зимние без шипов")])
        p._promoted_interim = ("зимние без", asyncio.get_running_loop().time())
        await p._transcript_reader_loop()
        assert p._final_transcript_queue.get_nowait().text == "зимние без шипов"

    @pytest.mark.asyncio
    async def test_word_fragment_is_not_a_repeat(self) -> None:
        # «ни» is inside «зимние» as letters, not as a word.
        p = _pipeline([_final("ни")])
        p._promoted_interim = ("зимние без", asyncio.get_running_loop().time())
        await p._transcript_reader_loop()
        assert p._final_transcript_queue.get_nowait().text == "ни"


class TestConfirmRepromptOnOpenQuestion:
    @pytest.mark.parametrize(
        "text",
        [
            "Є Спортрак за три тисячі, Ванлі за чотири гривень за шину. Яка підходить?",
            "Колір білий, правильно? Яка марка вашого автомобіля?",
            "Отже, вартість шиномонтажу в Дніпрі, вірно? Який діаметр коліс?",
            "Який час оберемо?",
            "255/50 ер вісімнадцять — зимові, вірно? Шиповані чи без шипів?",
            "Записуємо туди чи цікавить інша?",
            "Підтвердіть, будь ласка, діаметр 18 чи 17 дюймів записуємо?",
        ],
    )
    def test_open_last_question_is_not_yes_no(self, text: str) -> None:
        assert not _is_pending_confirmation(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Записуємо туди?",
            "Цей час підходить?",
            "Запис на десяту, все правильно?",
            "Обираєте Бріджстоун?",
            "Записуємо туди чи ні?",
            "Шиповані чи без шипів? Добре. Записуємо туди?",
        ],
    )
    def test_yes_no_question_still_is(self, text: str) -> None:
        assert _is_pending_confirmation(text)


class TestFinalDroppedWords:
    @pytest.mark.asyncio
    async def test_final_missing_interim_words_is_counted(self) -> None:
        # Call 78139ffb: interim «18 зимние», final «5 18».
        p = _pipeline(
            [
                Transcript(text="18 зимние", is_final=False, language="ru-RU"),
                Transcript(text="5 18", is_final=True, confidence=0.6, language="uk-UA"),
            ]
        )
        with patch("src.core.pipeline.stt_orphan_interim_total") as metric:
            await p._transcript_reader_loop()
        metric.labels.assert_called_once_with(event="final_dropped_words", language="uk-UA")
        assert p._final_transcript_queue.get_nowait().text == "5 18"  # the turn is unchanged

    @pytest.mark.asyncio
    async def test_final_covering_the_interim_is_not_counted(self) -> None:
        p = _pipeline([_interim("зимние без"), _final("Зимние без шипов.")])
        with patch("src.core.pipeline.stt_orphan_interim_total") as metric:
            await p._transcript_reader_loop()
        metric.labels.assert_not_called()
