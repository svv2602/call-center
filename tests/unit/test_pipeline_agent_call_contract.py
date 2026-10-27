"""Every keyword the pipeline passes to ``LLMAgent.process_message`` exists.

The pipeline tests drive the agent through ``AsyncMock``, which accepts any
keyword. So ``booking_gate_mode=`` (`0e82aa0`, a voice-loop-only gate) was
handed to the text agent's ``process_message``, which has no such
parameter, and every test stayed green. It is the non-streaming branch,
taken when ``FF_STREAMING_LLM`` is off, so turning streaming off as a
rollback would have raised ``TypeError`` on every turn. Found at the
multi-car acceptance (2026-10-27).

The contract is read from the source, so it holds for any branch, whether
a test drives it or not.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.agent.agent import LLMAgent

PIPELINE = Path(__file__).resolve().parents[2] / "src" / "core" / "pipeline.py"


def _agent_calls(tree: ast.AST) -> list[ast.Call]:
    """``self._agent.process_message(...)`` calls of the pipeline."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if (
            node.func.attr == "process_message"
            and isinstance(owner, ast.Attribute)
            and owner.attr == "_agent"
        ):
            calls.append(node)
    return calls


def test_the_pipeline_calls_the_text_agent() -> None:
    assert _agent_calls(ast.parse(PIPELINE.read_text()))


def test_every_keyword_the_pipeline_passes_is_a_parameter() -> None:
    params = set(inspect.signature(LLMAgent.process_message).parameters)
    for call in _agent_calls(ast.parse(PIPELINE.read_text())):
        passed = {kw.arg for kw in call.keywords if kw.arg is not None}
        unknown = passed - params
        assert not unknown, f"pipeline.py:{call.lineno} passes {sorted(unknown)}"


def test_the_multi_car_keywords_are_parameters() -> None:
    # Spread as **multi_car_kwargs, so the AST check above cannot see them.
    params = inspect.signature(LLMAgent.process_message).parameters
    assert "next_car_pending" in params
    assert "multi_car_over_cap" in params
