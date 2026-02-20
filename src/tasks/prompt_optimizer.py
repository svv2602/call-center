"""Automatic prompt optimization based on failed dialogues.

Analyzes low-quality calls, identifies failure patterns,
and generates improvement suggestions for manual review.
Results are persisted to the prompt_optimization_results table.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """\
You are a prompt engineer analyzing failed AI call center dialogues.
The call center sells tires in Ukraine. The bot speaks Ukrainian.

Below are transcriptions of calls that received low quality scores.
Analyze common failure patterns and suggest specific improvements
to the system prompt.

For each issue found, provide:
1. Pattern description (what went wrong)
2. Example from the transcription
3. Suggested prompt addition/modification

Respond with JSON:
{
  "patterns": [
    {
      "description": "Brief description of the failure pattern",
      "frequency": <number of calls with this pattern>,
      "severity": "high|medium|low",
      "example": "Quote from transcription",
      "suggestion": "Specific text to add/change in the system prompt"
    }
  ],
  "overall_recommendation": "Summary of main changes needed"
}

FAILED CALL TRANSCRIPTIONS:
"""


@app.task(name="src.tasks.prompt_optimizer.analyze_failed_calls")  # type: ignore[untyped-decorator]
def analyze_failed_calls(
    days: int = 7, max_calls: int = 20, triggered_by: str = "manual"
) -> dict[str, Any]:
    """Analyze recent low-quality calls and suggest prompt improvements.

    Args:
        days: Number of days to look back.
        max_calls: Maximum number of calls to analyze.
        triggered_by: Who triggered the analysis ('manual', 'beat').
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _analyze_failed_calls_async(days, max_calls, triggered_by)
        )
    finally:
        loop.close()


async def _analyze_failed_calls_async(
    days: int, max_calls: int, triggered_by: str
) -> dict[str, Any]:
    """Async implementation of failed calls analysis."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url)

    try:
        async with engine.begin() as conn:
            # Fetch low-quality calls with transcriptions
            result = await conn.execute(
                text("""
                    SELECT
                        c.id, c.scenario, c.quality_score, c.quality_details,
                        c.transferred_to_operator, c.transfer_reason
                    FROM calls c
                    WHERE c.quality_score IS NOT NULL
                      AND c.quality_score < :threshold
                      AND c.started_at > now() - make_interval(days => :days)
                    ORDER BY c.quality_score ASC
                    LIMIT :max_calls
                """),
                {
                    "threshold": settings.quality.score_threshold,
                    "days": days,
                    "max_calls": max_calls,
                },
            )
            calls = [dict(row._mapping) for row in result]

            if not calls:
                logger.info("No low-quality calls found in the last %d days", days)
                empty_result: dict[str, Any] = {
                    "patterns": [],
                    "overall_recommendation": "No issues found",
                    "calls_analyzed": 0,
                }
                await _persist_result(conn, days, 0, empty_result, triggered_by)
                return empty_result

            # Fetch transcriptions for each call
            transcriptions: list[str] = []
            for call in calls:
                turns_result = await conn.execute(
                    text("""
                        SELECT speaker, content
                        FROM call_turns
                        WHERE call_id = :call_id
                        ORDER BY turn_number
                    """),
                    {"call_id": str(call["id"])},
                )
                turns = turns_result.fetchall()

                lines = [f"--- Call {call['id']} (score: {call['quality_score']:.2f}) ---"]
                if call.get("scenario"):
                    lines.append(f"Scenario: {call['scenario']}")
                for turn in turns:
                    speaker = "Customer" if turn.speaker == "customer" else "Bot"
                    lines.append(f"{speaker}: {turn.content}")
                transcriptions.append("\n".join(lines))

        combined_text = "\n\n".join(transcriptions)

        # Analyze with LLM (try LLM router first, fall back to direct Anthropic)
        messages = [{"role": "user", "content": ANALYSIS_PROMPT + combined_text}]

        response_text = ""
        try:
            from src.llm import get_router

            llm_router = get_router()
            if llm_router is not None:
                from src.llm.models import LLMTask

                llm_response = await llm_router.complete(
                    LLMTask.PROMPT_OPTIMIZER,
                    messages,
                    max_tokens=1024,
                )
                response_text = llm_response.text.strip()
        except Exception:
            pass

        if not response_text:
            client = anthropic.Anthropic(api_key=settings.anthropic.api_key)
            response = client.messages.create(
                model=settings.quality.llm_model,
                max_tokens=1024,
                messages=messages,
            )
            content_block = response.content[0]
            response_text = content_block.text.strip() if hasattr(content_block, "text") else ""

        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        analysis = json.loads(response_text)
        analysis["calls_analyzed"] = len(calls)

        logger.info(
            "Prompt optimization analysis complete: %d calls analyzed, %d patterns found",
            len(calls),
            len(analysis.get("patterns", [])),
        )

        # Persist results
        async with engine.begin() as conn:
            await _persist_result(conn, days, len(calls), analysis, triggered_by)

        return analysis

    except json.JSONDecodeError:
        logger.exception("Failed to parse optimization analysis response")
        error_result: dict[str, Any] = {"error": "Failed to parse LLM response"}
        try:
            async with engine.begin() as conn:
                await _persist_result(
                    conn, days, 0, error_result, triggered_by, error="JSON parse error"
                )
        except Exception:
            pass
        return error_result
    finally:
        await engine.dispose()


async def _persist_result(
    conn: Any,
    days: int,
    calls_analyzed: int,
    analysis: dict[str, Any],
    triggered_by: str,
    error: str | None = None,
) -> None:
    """Save analysis result to prompt_optimization_results table."""
    patterns = json.dumps(analysis.get("patterns", []))
    recommendation = analysis.get("overall_recommendation", "")
    status = "error" if error else "completed"

    await conn.execute(
        text("""
            INSERT INTO prompt_optimization_results
                (days_analyzed, calls_analyzed, patterns, overall_recommendation,
                 status, error, triggered_by)
            VALUES (:days, :calls, :patterns::jsonb, :recommendation, :status, :error, :triggered_by)
        """),
        {
            "days": days,
            "calls": calls_analyzed,
            "patterns": patterns,
            "recommendation": recommendation,
            "status": status,
            "error": error,
            "triggered_by": triggered_by,
        },
    )
