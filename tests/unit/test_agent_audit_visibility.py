"""A failed tool-call audit write must be loud, and must not drop the call.

`ToolRouter.execute` writes one `call_tool_calls` row per tool call through the
`_on_execute` hook. That row is the only source of truth for "did this tool
actually run" — call reviews read its absence as "the tool was never called".
Until this wave the write was wrapped in `contextlib.suppress(Exception)`, so a
lost row left no log, no metric, and no way to tell a failed write from a tool
that was never invoked (37fb2d0: the same construct cost 3 of 3 bookings).

Three properties are asserted together, because any two without the third are a
regression:

* the failure is **visible** — ERROR log with `call_id` + tool name, and a
  metric sample that says *which* of the two call sites lost the row;
* the call **survives** — the tool result still comes back;
* `asyncio.shield` is **still there** — `transfer_to_operator` triggers an AMI
  redirect, the AudioSocket drops, `streaming_loop` is cancelled, and without
  the shield the audit write races that cancellation and loses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec

import pytest
from prometheus_client import REGISTRY

from src.agent.agent import ToolRouter
from src.llm.router import llm_call_id_var
from src.logging.call_logger import CallLogger

if TYPE_CHECKING:
    from collections.abc import Callable

_METRIC = "callcenter_tool_audit_write_failures_total"
_CALL_ID = "b7f1c0de-1111-2222-3333-444455556666"


def _failures(tool_name: str, path: str) -> float:
    """Current value of the audit-failure counter (0.0 when never touched)."""
    return REGISTRY.get_sample_value(_METRIC, {"tool_name": tool_name, "path": path}) or 0.0


def _the_audit_failure(caplog: pytest.LogCaptureFixture) -> str:
    """The single audit-failure line, or a readable assertion failure."""
    messages = [r.getMessage() for r in caplog.records if "audit write FAILED" in r.getMessage()]
    assert len(messages) == 1, f"expected exactly one audit failure log, got {messages}"
    return messages[0]


def _audit_hook(call_logger: Any) -> Callable[..., Any]:
    """The shape `src/main.py:1146` registers: a hook that writes one row.

    `call_logger` is autospecced, so a signature drift in `log_tool_call`
    breaks this test instead of silently keeping a dead path green
    (`codetrap_asyncmock_hides_missing_api.md`).
    """

    async def _on_tool_execute(
        name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: int,
        success: bool,
    ) -> None:
        await call_logger.log_tool_call(
            call_id=_CALL_ID,
            turn_number=1,
            tool_name=name,
            tool_args=args,
            tool_result=result,
            duration_ms=duration_ms,
            success=success,
        )

    return _on_tool_execute


def _router_with_failing_audit(
    tool_name: str,
    *,
    handler_raises: bool = False,
    exc: BaseException | None = None,
) -> ToolRouter:
    """Router whose audit write fails the way prod fails it.

    The default is `TypeError` because that is what actually reaches
    `execute()`: `CallLogger._execute` degrades DB outages to a Redis buffer,
    so the exception that escapes is the `json.dumps(..., ensure_ascii=False)`
    of a non-serializable tool arg or result.
    """
    call_logger = create_autospec(CallLogger, instance=True)
    call_logger.log_tool_call.side_effect = exc or TypeError(
        "Object of type Decimal is not JSON serializable"
    )

    router = ToolRouter()

    async def _handler(**_kwargs: Any) -> dict[str, Any]:
        if handler_raises:
            raise RuntimeError("store api exploded")
        return {"status": "ok"}

    router.register(tool_name, _handler)
    router.set_execute_hook(_audit_hook(call_logger))
    return router


@pytest.fixture(autouse=True)
def _call_id_in_context() -> Any:
    """Pin the contextvar `src/main.py:1251` sets before the pipeline runs."""
    token = llm_call_id_var.set(_CALL_ID)
    yield
    llm_call_id_var.reset(token)


class TestTheFailureIsVisible:
    """An audit write that fails leaves a log line and a metric sample."""

    async def test_result_path_logs_error_with_call_id_and_tool_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        router = _router_with_failing_audit("transfer_to_operator")

        with caplog.at_level(logging.ERROR, logger="src.agent.agent"):
            await router.execute("transfer_to_operator", {"reason": "cannot_help"})

        records = [r for r in caplog.records if "audit write FAILED" in r.getMessage()]
        assert len(records) == 1, "exactly one audit failure must be reported"
        record = records[0]
        assert record.levelno == logging.ERROR
        assert "transfer_to_operator" in record.getMessage()
        assert _CALL_ID in record.getMessage()
        assert record.call_id == _CALL_ID  # structured_logger.py:30 reads this
        assert record.exc_info is not None, "the traceback is the diagnosis"

    async def test_result_path_names_itself_and_not_the_error_path(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The two call sites are externally identical — the log must separate them.

        Without `path=` in the message, a defect in one block reads exactly like
        a defect in the other (`feedback_earlier_guard_hides_later.md`).
        """
        router = _router_with_failing_audit("book_fitting")

        with caplog.at_level(logging.ERROR, logger="src.agent.agent"):
            await router.execute("book_fitting", {"station_id": "7"})

        message = _the_audit_failure(caplog)
        assert "path=result" in message
        assert "path=error" not in message

    async def test_error_path_names_itself_and_not_the_result_path(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The tool raised; its audit row is lost too, and that is a separate site."""
        router = _router_with_failing_audit("book_fitting", handler_raises=True)

        with caplog.at_level(logging.ERROR, logger="src.agent.agent"):
            await router.execute("book_fitting", {"station_id": "7"})

        message = _the_audit_failure(caplog)
        assert "path=error" in message
        assert "path=result" not in message
        assert "book_fitting" in message
        assert _CALL_ID in message

    async def test_result_path_increments_only_its_own_metric_label(self) -> None:
        before_result = _failures("transfer_to_operator", "result")
        before_error = _failures("transfer_to_operator", "error")

        router = _router_with_failing_audit("transfer_to_operator")
        await router.execute("transfer_to_operator", {"reason": "cannot_help"})

        assert _failures("transfer_to_operator", "result") == before_result + 1
        assert _failures("transfer_to_operator", "error") == before_error

    async def test_error_path_increments_only_its_own_metric_label(self) -> None:
        before_result = _failures("get_fitting_slots", "result")
        before_error = _failures("get_fitting_slots", "error")

        router = _router_with_failing_audit("get_fitting_slots", handler_raises=True)
        await router.execute("get_fitting_slots", {"date": "2026-09-12"})

        assert _failures("get_fitting_slots", "error") == before_error + 1
        assert _failures("get_fitting_slots", "result") == before_result

    async def test_a_healthy_audit_write_reports_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The counter must stay a signal, not a background hum."""
        before = _failures("search_tires", "result")
        call_logger = create_autospec(CallLogger, instance=True)
        router = ToolRouter()

        async def _handler(**_kwargs: Any) -> dict[str, Any]:
            return {"items": []}

        router.register("search_tires", _handler)
        router.set_execute_hook(_audit_hook(call_logger))

        with caplog.at_level(logging.ERROR, logger="src.agent.agent"):
            result = await router.execute("search_tires", {"width": 205})

        assert result == {"items": []}
        assert call_logger.log_tool_call.await_count == 1
        assert _failures("search_tires", "result") == before
        assert not [r for r in caplog.records if "audit write FAILED" in r.getMessage()]


class TestTheCallSurvives:
    """Silent loss is traded for loud loss, not for a dropped call."""

    async def test_tool_result_is_returned_despite_a_failed_audit_write(self) -> None:
        router = _router_with_failing_audit("search_tires")

        result = await router.execute("search_tires", {"width": 205})

        assert result == {"status": "ok"}

    async def test_tool_error_is_returned_despite_a_failed_audit_write(self) -> None:
        router = _router_with_failing_audit("book_fitting", handler_raises=True)

        result = await router.execute("book_fitting", {"station_id": "7"})

        assert result == {"error": "store api exploded"}


class TestTheShieldStillCoversTheCancellationRace:
    """`transfer_to_operator` → AMI redirect → AudioSocket drop → cancellation.

    The audit write must outlive the task that started it. Without the shield
    the row for the single most important tool call is the one guaranteed to be
    lost, which is how the original code got written.
    """

    async def test_audit_write_completes_after_the_caller_is_cancelled(self) -> None:
        written: list[str] = []
        reached_hook = asyncio.Event()
        router = ToolRouter()

        async def _handler(**_kwargs: Any) -> dict[str, Any]:
            return {"status": "transferring"}

        async def _slow_audit_write(
            name: str,
            args: dict[str, Any],
            result: Any,
            duration_ms: int,
            success: bool,
        ) -> None:
            reached_hook.set()
            await asyncio.sleep(0.05)
            written.append(name)

        router.register("transfer_to_operator", _handler)
        router.set_execute_hook(_slow_audit_write)

        task = asyncio.create_task(router.execute("transfer_to_operator", {"reason": "complaint"}))
        await asyncio.wait_for(reached_hook.wait(), timeout=1.0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.2)
        assert written == ["transfer_to_operator"], (
            "the shielded audit write must survive cancellation of streaming_loop"
        )

    async def test_cancellation_is_not_recorded_as_an_audit_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`CancelledError` is a BaseException and must pass straight through.

        Counting it as a lost row would poison the metric the wave exists to
        read, and swallowing it would break cancellation of the call.
        """
        before = _failures("transfer_to_operator", "result")
        router = _router_with_failing_audit("transfer_to_operator", exc=asyncio.CancelledError())

        with (
            caplog.at_level(logging.ERROR, logger="src.agent.agent"),
            pytest.raises(asyncio.CancelledError),
        ):
            await router.execute("transfer_to_operator", {"reason": "complaint"})

        assert _failures("transfer_to_operator", "result") == before
        assert not [r for r in caplog.records if "audit write FAILED" in r.getMessage()]
