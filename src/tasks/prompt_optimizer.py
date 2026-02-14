"""Automatic prompt optimization based on failed dialogues.

Analyzes low-quality calls, identifies failure patterns,
and generates improvement suggestions for manual review.
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
def analyze_failed_calls(days: int = 7, max_calls: int = 20) -> dict[str, Any]:
    """Analyze recent low-quality calls and suggest prompt improvements.

    Args:
        days: Number of days to look back.
        max_calls: Maximum number of calls to analyze.
    """
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_analyze_failed_calls_async(days, max_calls))


async def _analyze_failed_calls_async(days: int, max_calls: int) -> dict[str, Any]:
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
                return {"patterns": [], "overall_recommendation": "No issues found"}

            # Fetch transcriptions for each call
            transcriptions: list[str] = []
            for call in calls:
                turns_result = await conn.execute(
                    text("""
                        SELECT speaker, text
                        FROM call_turns
                        WHERE call_id = :call_id
                        ORDER BY turn_index
                    """),
                    {"call_id": str(call["id"])},
                )
                turns = turns_result.fetchall()

                lines = [f"--- Call {call['id']} (score: {call['quality_score']:.2f}) ---"]
                if call.get("scenario"):
                    lines.append(f"Scenario: {call['scenario']}")
                for turn in turns:
                    speaker = "Customer" if turn.speaker == "customer" else "Bot"
                    lines.append(f"{speaker}: {turn.text}")
                transcriptions.append("\n".join(lines))

        combined_text = "\n\n".join(transcriptions)

        # Analyze with Claude
        client = anthropic.Anthropic(api_key=settings.anthropic.api_key)
        response = client.messages.create(
            model=settings.quality.llm_model,
            max_tokens=1024,
            messages=[
                {"role": "user", "content": ANALYSIS_PROMPT + combined_text},
            ],
        )

        content_block = response.content[0]
        response_text = content_block.text.strip() if hasattr(content_block, "text") else ""
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        analysis = json.loads(response_text)

        logger.info(
            "Prompt optimization analysis complete: %d calls analyzed, %d patterns found",
            len(calls),
            len(analysis.get("patterns", [])),
        )

        analysis_result: dict[str, Any] = analysis
        return analysis_result

    except json.JSONDecodeError:
        logger.exception("Failed to parse optimization analysis response")
        return {"error": "Failed to parse LLM response"}
    finally:
        await engine.dispose()
