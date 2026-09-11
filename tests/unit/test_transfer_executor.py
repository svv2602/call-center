"""Волна 2-C — у перевода к оператору один исполнитель, и обещание идёт за ним.

За 30 дней в проде 15 звонков получили `transfer_reason='intent_classifier_transfer'`.
На всех 15 клиент услышал вариант шаблона `transfer` («з'єдную вас з оператором») и
остался с ботом: путь классификатора звал `CallSession.mark_transfer` напрямую и ставил
флаг, ни разу не спросив AMI. Те же 30 дней дали 53 звонка, где редирект действительно
состоялся, — и на них шаблон не прозвучал ни разу, потому что AMI рвёт канал раньше.
То есть объявление `_close_turn` звучало РОВНО на сломанном пути.

Здесь пинуются три вещи:

  * оба пути — инструмента и классификатора — приводят к одному и тому же исполнителю,
    и следствий у них три одинаковых: редирект, метрика, шаблон;
  * ни один исход, кроме принятого редиректа, обещания не произносит — включая исходы,
    которых сегодня не существует (default-deny);
  * проводка отдельно от предикатов: `_close_turn` и точка вызова в `_maybe_handle_intent`
    проверяются своими тестами, потому что корпусный тест на предикат проводку не
    покрывает.

Коллабораторы — `create_autospec` по настоящим классам (`AsteriskAMIClient`,
`StoreClient`) и `MagicMock(spec=[...])`. Голый `AsyncMock` отвечает на любой атрибут и
однажды позеленил 65 тестов против метода, которого в проде не было.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

import src.main as main_module
from src.core.asterisk_ami import AsteriskAMIClient
from src.core.call_session import TRANSFER_OUTCOME_INITIATED, CallSession, CallState
from src.core.pipeline import CallPipeline
from src.monitoring.metrics import transfer_attempts_total
from src.store_client.client import StoreClient
from src.stt.base import Transcript

if TYPE_CHECKING:
    from src.agent.agent import ToolRouter

CHANNEL_NAME = "SIP/trunk1058-00000042"
TRANSFER_TEMPLATE = "Зачекайте, будь ласка, з'єдную вас з оператором."

#: Открыто круглосуточно: `working_hours=None` в `is_open` означает 24/7.
OPEN_ALWAYS: dict[str, Any] | None = None
#: Закрыто в любой день недели — каждый день `null`.
CLOSED_ALWAYS: dict[str, Any] = {
    "timezone": "Europe/Kyiv",
    "mon": None,
    "tue": None,
    "wed": None,
    "thu": None,
    "fri": None,
    "sat": None,
    "sun": None,
}


def _metric(result: str) -> float:
    """Текущее значение `transfer_attempts_total{result=...}`.

    Счётчик глобальный и переживает тесты, поэтому проверяется дельта, а не
    абсолютное значение.
    """
    return transfer_attempts_total.labels(result=result)._value.get()


class Harness:
    """Настоящий `transfer_to_operator` из `src/main.py` плюс настоящий `_close_turn`.

    Обработчик — замыкание над `session`, `_ami_client`, `_redis` и `publish_event`,
    поэтому собрать его можно только через `_build_tool_router` с подменёнными
    глобалами модуля. Пересобирать его копией внутри теста нельзя: тест, который
    воспроизводит логику, проверяет свою копию, а не прод.
    """

    def __init__(
        self,
        *,
        redirect_result: bool | BaseException = True,
        channel_name: str | None = CHANNEL_NAME,
        working_hours: dict[str, Any] | None = OPEN_ALWAYS,
        ami_available: bool = True,
    ) -> None:
        self.session = CallSession(uuid.uuid4())
        self.session.working_hours = working_hours
        self.session.transition_to(CallState.GREETING)
        self.session.transition_to(CallState.LISTENING)

        self.ami: Any = None
        if ami_available:
            self.ami = create_autospec(AsteriskAMIClient, instance=True)
            if isinstance(redirect_result, BaseException):
                self.ami.redirect.side_effect = redirect_result
            else:
                self.ami.redirect.return_value = redirect_result

        self.redis = MagicMock(spec=["get"])
        self.redis.get = AsyncMock(
            return_value=None if channel_name is None else channel_name.encode()
        )

        self.published: list[tuple[str, dict[str, Any]]] = []

        async def _publish(event_type: str, data: dict[str, Any] | None = None) -> None:
            self.published.append((event_type, data or {}))

        self._publish = _publish
        self.store_client = create_autospec(StoreClient, instance=True)

        #: (tool_name, args, result) по каждому исполнению — то, из чего роутер
        #: делает строку `call_tool_calls`. Тест утверждает ЧЕЙ код отработал,
        #: а не только внешний результат.
        self.audit: list[tuple[str, dict[str, Any], Any]] = []

        with (
            patch.object(main_module, "_ami_client", self.ami),
            patch.object(main_module, "_redis", self.redis),
            patch.object(main_module, "publish_event", _publish),
        ):
            self.router: ToolRouter = main_module._build_tool_router(
                self.session, store_client=self.store_client
            )

        async def _hook(
            name: str, args: dict[str, Any], result: Any, duration_ms: int, success: bool
        ) -> None:
            self.audit.append((name, args, result))

        self.router.set_execute_hook(_hook)

        # --- pipeline -------------------------------------------------------
        self.spoken: list[str] = []
        conn = MagicMock(spec=["is_closed"])
        conn.is_closed = False

        self.llm_router = MagicMock(spec=["complete"])
        streaming_loop = MagicMock(spec=["run_turn", "_llm_router", "_tool_router"])
        streaming_loop._llm_router = self.llm_router
        streaming_loop._tool_router = self.router

        self.pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(spec=[]),
            tts=MagicMock(spec=[]),
            agent=MagicMock(spec=[]),
            session=self.session,
            streaming_loop=streaming_loop,
            db_engine=None,
        )

        async def _speak(text: str) -> None:
            self.spoken.append(text)

        self.pipeline._speak = _speak  # type: ignore[method-assign]
        self.pipeline._speak_streaming = _speak  # type: ignore[method-assign]
        self.pipeline._templates = {"transfer": TRANSFER_TEMPLATE}

    def patched(self) -> Any:
        """Контекст, в котором замыкание видит подменённые глобалы main.py."""
        return _Patched(self)

    async def run_tool_path(self, reason: str = "customer_request") -> Any:
        """Путь инструмента: LLM позвал `transfer_to_operator` через роутер."""
        with self.patched():
            return await self.router.execute("transfer_to_operator", {"reason": reason})

    async def run_classifier_path(self, text: str = "дайте оператора") -> bool:
        """Путь классификатора: вердикт TRANSFER через настоящий `_maybe_handle_intent`.

        Вход — именно `_maybe_handle_intent`, а не `_dispatch_transfer_verdict`:
        точка вызова — отдельная мутация, и корпусный тест на обработчик её не
        покрывает.
        """
        transcript = Transcript(text=text, is_final=True, confidence=0.95, language="uk-UA")
        verdict = SimpleNamespace(primary_intent="TRANSFER", confidence=0.95)

        async def _classify(**_kwargs: Any) -> Any:
            return verdict

        with self.patched(), patch("src.agent.intent_classifier.classify_intent", _classify):
            return await self.pipeline._maybe_handle_intent(transcript)

    async def close_turn(self) -> bool:
        return await self.pipeline._close_turn()


class _Patched:
    def __init__(self, harness: Harness) -> None:
        self._patches = [
            patch.object(main_module, "_ami_client", harness.ami),
            patch.object(main_module, "_redis", harness.redis),
            patch.object(main_module, "publish_event", harness._publish),
        ]

    def __enter__(self) -> None:
        for p in self._patches:
            p.start()

    def __exit__(self, *exc: Any) -> None:
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------------
# 4.1 — оба пути дают одни и те же три следствия
# ---------------------------------------------------------------------------


class TestBothPathsReachTheOperator:
    @pytest.mark.asyncio
    async def test_tool_path_redirects_counts_and_promises(self) -> None:
        h = Harness()
        before = _metric("success")

        result = await h.run_tool_path()

        # 1. редирект отправлен — и именно в контекст перевода, по имени канала
        h.ami.redirect.assert_awaited_once()
        kwargs = h.ami.redirect.await_args.kwargs
        assert kwargs["channel_name"] == CHANNEL_NAME
        assert kwargs["context"] == "transfer-to-operator"
        # 2. метрика
        assert _metric("success") == before + 1
        # 3. обещание — но только через `_close_turn`, см. отдельный тест проводки
        assert result["status"] == "transferring"
        assert h.session.transfer_redirect_initiated() is True
        assert await h.close_turn() is True
        assert h.spoken == [TRANSFER_TEMPLATE]

    @pytest.mark.asyncio
    async def test_classifier_path_gets_the_same_three(self) -> None:
        """Ядро волны: до правки путь классификатора не делал ни одного из трёх."""
        h = Harness()
        before = _metric("success")

        took_turn = await h.run_classifier_path()

        assert took_turn is True
        h.ami.redirect.assert_awaited_once()
        assert h.ami.redirect.await_args.kwargs["channel_name"] == CHANNEL_NAME
        assert _metric("success") == before + 1
        assert h.session.transfer_redirect_initiated() is True
        assert await h.close_turn() is True
        assert h.spoken == [TRANSFER_TEMPLATE]

    @pytest.mark.asyncio
    async def test_classifier_path_runs_the_transfer_tool_itself(self) -> None:
        """ЧЕЙ код отработал, а не только чем кончилось.

        Прод в логе называет только первый сработавший гард, поэтому тест,
        проверяющий лишь внешний результат, не отличает «сработал общий
        исполнитель» от «кто-то поставил те же флаги рядом». Здесь утверждается
        имя инструмента и причина, с которой его позвали, — то же, что попадёт
        в `call_tool_calls`.
        """
        h = Harness()

        await h.run_classifier_path()

        assert [name for name, _args, _res in h.audit] == ["transfer_to_operator"]
        _name, args, result = h.audit[0]
        assert args == {"reason": "intent_classifier_transfer"}
        assert result["status"] == "transferring"
        assert h.session.transfer_reason == "intent_classifier_transfer"

    @pytest.mark.asyncio
    async def test_classifier_path_publishes_the_transfer_event(self) -> None:
        """Побочные эффекты исполнителя достаются и второму пути целиком."""
        h = Harness()

        await h.run_classifier_path()

        assert h.published == [("call:transferred", {"call_id": str(h.session.channel_uuid)})]

    def test_pipeline_no_longer_marks_transfer_by_itself(self) -> None:
        """`mark_transfer` зовёт только исполнитель.

        Структурная проверка, а не поведенческая: вторая точка вызова —
        это ровно тот дефект, который волна закрывает, и обнаружить её надо
        при чтении, а не через месяц в проде.
        """
        root = Path(__file__).resolve().parents[2]
        pipeline_src = (root / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
        assert "mark_transfer(" not in pipeline_src

        main_src = (root / "src" / "main.py").read_text(encoding="utf-8")
        assert main_src.count("session.mark_transfer(") == 1


# ---------------------------------------------------------------------------
# 4.2 — after-hours
# ---------------------------------------------------------------------------


class TestAfterHours:
    @pytest.mark.asyncio
    async def test_tool_path_after_hours_does_not_promise(self) -> None:
        h = Harness(working_hours=CLOSED_ALWAYS)
        before = _metric("after_hours")

        result = await h.run_tool_path()

        assert result["status"] == "after_hours"
        assert _metric("after_hours") == before + 1
        h.ami.redirect.assert_not_awaited()
        assert h.session.transfer_redirect_initiated() is False
        assert await h.close_turn() is False
        assert h.spoken == []

    @pytest.mark.asyncio
    async def test_classifier_path_after_hours_does_not_promise(self) -> None:
        """Проверяется и на пути классификатора — там проверки часов не было вовсе."""
        h = Harness(working_hours=CLOSED_ALWAYS)

        took_turn = await h.run_classifier_path()

        # Ход не взят: клиент услышит ответ LLM про обратный звонок, а не тишину.
        assert took_turn is False
        h.ami.redirect.assert_not_awaited()
        assert h.session.transferred is False
        assert h.session.transfer_outcome == "after_hours"
        assert await h.close_turn() is False
        assert h.spoken == []


# ---------------------------------------------------------------------------
# 4.3 — отказ AMI
# ---------------------------------------------------------------------------


class TestAmiRefusal:
    @pytest.mark.asyncio
    async def test_redirect_returns_false_no_promise_and_loud(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        h = Harness(redirect_result=False)
        before = _metric("error")

        with caplog.at_level("ERROR"):
            result = await h.run_tool_path()

        assert result["status"] == "error"
        assert _metric("error") == before + 1
        assert h.session.transferred is False
        assert h.session.transfer_outcome == "error"
        assert await h.close_turn() is False
        assert h.spoken == []
        assert any("AMI transfer failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_redirect_raises_no_promise_and_loud(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Исключение AMI — это отказ перевода, а не молча «перевод инициирован».

        `contextlib.suppress` на этом пути запрещён: проглоченный сбой
        неотличим от самого дефекта волны.
        """
        h = Harness(redirect_result=OSError("AMI socket gone"))

        with caplog.at_level("ERROR"):
            result = await h.run_tool_path()

        assert result["status"] == "error"
        assert h.session.transferred is False
        assert h.session.transfer_outcome == "error"
        assert await h.close_turn() is False
        assert h.spoken == []
        assert any("AMI transfer raised" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_channel_mapping_no_promise(self) -> None:
        h = Harness(channel_name=None)

        result = await h.run_tool_path()

        assert result["status"] == "error"
        h.ami.redirect.assert_not_awaited()
        assert await h.close_turn() is False
        assert h.spoken == []

    @pytest.mark.asyncio
    async def test_no_ami_client_no_promise(self) -> None:
        h = Harness(ami_available=False)
        before = _metric("unavailable")

        result = await h.run_tool_path()

        assert result["status"] == "unavailable"
        assert _metric("unavailable") == before + 1
        assert h.session.transfer_outcome == "unavailable"
        assert await h.close_turn() is False
        assert h.spoken == []

    @pytest.mark.asyncio
    async def test_classifier_path_ami_refusal_falls_through_to_the_llm(self) -> None:
        """Отказ на пути классификатора: ход отдан LLM, звонок не зависает."""
        h = Harness(redirect_result=False)

        took_turn = await h.run_classifier_path()

        assert took_turn is False
        assert h.session.transferred is False
        assert h.session.transfer_outcome == "error"
        # Реплика клиента здесь НЕ записана: её запишет обычный ход LLM,
        # иначе она удвоится в `dialog_history` и в `call_turns`.
        assert h.session.dialog_history == []


# ---------------------------------------------------------------------------
# 4.4 — проводка в `_close_turn`
# ---------------------------------------------------------------------------


class TestCloseTurnWiring:
    """Отдельно от исполнителя: корпусный тест проводку не покрывает."""

    def _pipeline(self, session: CallSession) -> tuple[CallPipeline, list[str]]:
        spoken: list[str] = []
        conn = MagicMock(spec=["is_closed"])
        conn.is_closed = False
        pipeline = CallPipeline(
            conn=conn,
            stt=MagicMock(spec=[]),
            tts=MagicMock(spec=[]),
            agent=MagicMock(spec=[]),
            session=session,
            streaming_loop=MagicMock(spec=["run_turn", "_llm_router", "_tool_router"]),
            db_engine=None,
        )

        async def _speak(text: str) -> None:
            spoken.append(text)

        pipeline._speak = _speak  # type: ignore[method-assign]
        pipeline._templates = {"transfer": TRANSFER_TEMPLATE}
        return pipeline, spoken

    @pytest.mark.asyncio
    async def test_initiated_announces_and_stops_the_loop(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.mark_transfer("customer_request")
        pipeline, spoken = self._pipeline(session)

        assert await pipeline._close_turn() is True
        assert spoken == [TRANSFER_TEMPLATE]
        assert session.state is CallState.TRANSFERRING

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome",
        [None, "after_hours", "error", "unavailable", "queued_for_wave_9", ""],
    )
    async def test_every_other_outcome_refuses_the_promise(self, outcome: str | None) -> None:
        """Default-deny по всему пространству исходов, а не по списку известных.

        `queued_for_wave_9` в коде не существует и существовать не должен: именно
        значение, которого никто не предвидел, должно отказываться само, без
        правки гарда.
        """
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.transfer_outcome = outcome
        pipeline, spoken = self._pipeline(session)

        assert await pipeline._close_turn() is False
        assert spoken == []
        assert session.state is CallState.LISTENING

    @pytest.mark.asyncio
    async def test_bare_transferred_flag_is_not_enough(self) -> None:
        """Голого флага мало — это и есть дефект 15 звонков.

        Флаг восстанавливается из Redis и служит диспозицией звонка в трёх
        местах `main.py`; «можно ли обещать перевод» — не его смысл.
        """
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.transferred = True
        session.transfer_reason = "intent_classifier_transfer"
        pipeline, spoken = self._pipeline(session)

        assert await pipeline._close_turn() is False
        assert spoken == []


# ---------------------------------------------------------------------------
# исход переживает восстановление сессии из Redis
# ---------------------------------------------------------------------------


class TestOutcomeSurvivesSerialization:
    def test_roundtrip_keeps_the_outcome(self) -> None:
        session = CallSession(uuid.uuid4())
        session.transition_to(CallState.GREETING)
        session.transition_to(CallState.LISTENING)
        session.mark_transfer("customer_request")

        restored = CallSession.from_dict(session.to_dict())

        assert restored.transfer_outcome == TRANSFER_OUTCOME_INITIATED
        assert restored.transfer_redirect_initiated() is True

    def test_roundtrip_of_a_failed_transfer_stays_failed(self) -> None:
        session = CallSession(uuid.uuid4())
        session.mark_transfer_failed("intent_classifier_transfer", "error")

        restored = CallSession.from_dict(session.to_dict())

        assert restored.transferred is False
        assert restored.transfer_redirect_initiated() is False
