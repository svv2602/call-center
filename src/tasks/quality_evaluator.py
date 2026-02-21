"""Automatic call quality evaluation using Claude Haiku.

Evaluates each call against 8 quality criteria after completion.
Flags problematic calls (score < threshold) for manual review.
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

# Shared LLM router reference (set from main.py when FF_LLM_ROUTING_ENABLED=true)
_llm_router_ref: object | None = None


def set_llm_router(router: object | None) -> None:
    """Set the shared LLM router for quality evaluation tasks."""
    global _llm_router_ref
    _llm_router_ref = router


def _get_llm_router() -> object | None:
    """Get the shared LLM router (or None if not configured)."""
    return _llm_router_ref


QUALITY_CRITERIA = [
    "bot_greeted_properly",
    "bot_understood_intent",
    "bot_used_correct_tool",
    "bot_provided_accurate_info",
    "bot_confirmed_before_action",
    "bot_was_concise",
    "call_resolved_without_human",
    "customer_seemed_satisfied",
]

EVALUATION_PROMPT = """\
You are a quality evaluator for an AI call center that sells tires in Ukraine.
Analyze the following call transcription and evaluate the bot's performance.

For each criterion, provide a score from 0.0 to 1.0:
- 1.0 = perfect performance
- 0.5 = acceptable but could improve
- 0.0 = failed completely
- If a criterion is not applicable to this call, score it as 1.0.

Criteria:
1. bot_greeted_properly — The bot greeted the customer appropriately
2. bot_understood_intent — The bot correctly understood the customer's request
3. bot_used_correct_tool — The bot called the right tool/API for the task
4. bot_provided_accurate_info — The information provided was correct
5. bot_confirmed_before_action — The bot confirmed before creating orders/bookings
6. bot_was_concise — The bot's responses were concise and to the point
7. call_resolved_without_human — The call was resolved without operator transfer
8. customer_seemed_satisfied — The customer did not express dissatisfaction

Respond ONLY with a JSON object like:
{
  "bot_greeted_properly": 0.9,
  "bot_understood_intent": 0.8,
  "bot_used_correct_tool": 1.0,
  "bot_provided_accurate_info": 0.7,
  "bot_confirmed_before_action": 1.0,
  "bot_was_concise": 0.8,
  "call_resolved_without_human": 1.0,
  "customer_seemed_satisfied": 0.9,
  "comment": "Brief explanation of notable issues if any"
}

TRANSCRIPTION:
"""


def _build_transcription_text(turns: list[dict[str, Any]]) -> str:
    """Format call turns into readable transcription."""
    lines: list[str] = []
    for turn in turns:
        speaker = turn.get("speaker", "unknown")
        text_content = turn.get("content", "")
        tool_calls = turn.get("tool_calls", [])

        if text_content:
            label = "Customer" if speaker == "customer" else "Bot"
            lines.append(f"{label}: {text_content}")

        for tc in tool_calls:
            lines.append(f"  [Tool: {tc.get('tool_name', '?')} → {tc.get('success', '?')}]")

    return "\n".join(lines)


@app.task(
    name="src.tasks.quality_evaluator.evaluate_call_quality",
    bind=True,
    max_retries=3,
    soft_time_limit=120,
    time_limit=150,
)  # type: ignore[untyped-decorator]
def evaluate_call_quality(self: Any, call_id: str) -> dict[str, Any]:
    """Evaluate call quality using Claude Haiku.

    Args:
        call_id: UUID of the call to evaluate.

    Returns:
        Quality evaluation result with per-criterion scores.
    """
    import asyncio

    return asyncio.run(_evaluate_call_quality_async(self, call_id))


async def _evaluate_call_quality_async(task: Any, call_id: str) -> dict[str, Any]:
    """Async implementation of quality evaluation."""
    settings = get_settings()
    engine = create_async_engine(settings.database.url, pool_pre_ping=True)

    try:
        # Fetch call turns from database
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT speaker, content, stt_confidence
                    FROM call_turns
                    WHERE call_id = :call_id
                    ORDER BY turn_number
                """),
                {"call_id": call_id},
            )
            turns = [dict(row._mapping) for row in result]

            # Fetch tool calls
            tc_result = await conn.execute(
                text("""
                    SELECT tool_name, success
                    FROM call_tool_calls
                    WHERE call_id = :call_id
                    ORDER BY created_at
                """),
                {"call_id": call_id},
            )
            tool_calls = [dict(row._mapping) for row in tc_result]

            # Fetch call metadata
            call_result = await conn.execute(
                text("""
                    SELECT transferred_to_operator, transfer_reason, scenario
                    FROM calls
                    WHERE id = :call_id
                """),
                {"call_id": call_id},
            )
            call_row = call_result.first()

        if not turns:
            logger.warning("No turns found for call %s, skipping quality evaluation", call_id)
            return {"call_id": call_id, "error": "no_turns"}

        # Enrich turns with tool call info
        turn_data = [{"speaker": t["speaker"], "content": t["content"]} for t in turns]
        if tool_calls:
            turn_data.append({"tool_calls": tool_calls})

        transcription = _build_transcription_text(turn_data)

        # Add call metadata context
        if call_row:
            context_parts = []
            if call_row.transferred_to_operator:
                context_parts.append(
                    f"Call was transferred to operator. Reason: {call_row.transfer_reason}"
                )
            if call_row.scenario:
                context_parts.append(f"Scenario: {call_row.scenario}")
            if context_parts:
                transcription = "\n".join(context_parts) + "\n\n" + transcription

        # Call LLM for evaluation (router or direct Anthropic)
        messages = [{"role": "user", "content": EVALUATION_PROMPT + transcription}]

        llm_router = _get_llm_router()
        if llm_router is not None:
            from src.llm.models import LLMTask

            llm_response = await llm_router.complete(
                LLMTask.QUALITY_SCORING,
                messages,
                max_tokens=512,
            )
            response_text = llm_response.text.strip()
        else:
            client = anthropic.Anthropic(api_key=settings.anthropic.api_key)
            response = client.messages.create(
                model=settings.quality.llm_model,
                max_tokens=512,
                messages=messages,
            )
            content_block = response.content[0]
            response_text = content_block.text.strip() if hasattr(content_block, "text") else ""

        # Parse JSON response
        # Handle markdown code blocks if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        quality_details = json.loads(response_text)

        # Calculate overall score
        scores = [quality_details.get(criterion, 0.0) for criterion in QUALITY_CRITERIA]
        overall_score = sum(scores) / len(scores) if scores else 0.0

        # Check if call needs manual review
        needs_review = overall_score < settings.quality.score_threshold

        # Save to database
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE calls
                    SET quality_score = :score,
                        quality_details = :details
                    WHERE id = :call_id
                """),
                {
                    "call_id": call_id,
                    "score": overall_score,
                    "details": json.dumps(quality_details),
                },
            )

        if needs_review:
            logger.warning(
                "Call %s flagged for review: quality_score=%.2f (threshold=%.2f)",
                call_id,
                overall_score,
                settings.quality.score_threshold,
            )

        logger.info(
            "Quality evaluation complete: call_id=%s, score=%.2f, needs_review=%s",
            call_id,
            overall_score,
            needs_review,
        )

        return {
            "call_id": call_id,
            "quality_score": overall_score,
            "quality_details": quality_details,
            "needs_review": needs_review,
        }

    except json.JSONDecodeError as err:
        logger.exception("Failed to parse quality evaluation response for call %s", call_id)
        raise task.retry(countdown=60) from err
    except anthropic.APIError as err:
        logger.exception("Anthropic API error during quality evaluation for call %s", call_id)
        raise task.retry(countdown=30) from err
    finally:
        await engine.dispose()
