"""Generate guidance notes for conversation patterns via LLM.

Uses LLM router (with fallback providers) or direct Anthropic SDK
to draft a system prompt instruction from a turn group's dialogue fragment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import anthropic

from src.config import get_settings

if TYPE_CHECKING:
    from anthropic.types import MessageParam

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты эксперт по quality assurance в колл-центре интернет-магазина шин (Украина).
На основе фрагмента диалога (реплики клиента и агента) сформулируй краткую инструкцию
для системного промпта ИИ-агента.

Инструкция должна быть:
- На русском языке (это служебный текст для промпта, не для клиента)
- Краткой (2-4 предложения)
- Конкретной — описывать правило поведения агента
- В формате директивы: «Агент должен...» / «При ... агент обязан...»

НЕ добавляй пояснения, вступления или заключения — только саму инструкцию."""


def _build_user_message(
    turns: list[dict[str, Any]],
    intent_label: str,
    pattern_type: str,
    correction: str | None,
) -> str:
    """Build user message for guidance generation."""
    lines = [f"Интент: {intent_label}", f"Тип: {pattern_type}", "", "Диалог:"]
    for turn in turns:
        speaker = "Клиент" if turn.get("speaker") == "customer" else "Агент"
        lines.append(f"  {speaker}: {turn.get('content', '')}")

    if pattern_type == "positive":
        lines.append("")
        lines.append("Опиши, что агент делает правильно, и сформулируй правило для промпта.")
    elif pattern_type == "negative":
        lines.append("")
        if correction:
            lines.append(f"Исправление: {correction}")
        lines.append("Опиши ошибку агента и сформулируй правило для её исправления.")

    return "\n".join(lines)


async def generate_guidance(
    turns: list[dict[str, Any]],
    intent_label: str,
    pattern_type: str,
    correction: str | None,
) -> str:
    """Generate a guidance note draft for a turn group.

    Args:
        turns: List of turn dicts with 'speaker' and 'content' keys.
        intent_label: The intent label for this group.
        pattern_type: 'positive' or 'negative'.
        correction: Optional correction text (for negative patterns).

    Returns:
        Generated guidance note text, or empty string on failure.
    """
    user_message = _build_user_message(turns, intent_label, pattern_type, correction)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    # Try LLM router first
    try:
        reply = await _generate_via_router(_SYSTEM_PROMPT, messages)
        if reply:
            return reply
    except Exception:
        logger.debug("LLM router not available for guidance generation, trying direct SDK")

    # Fallback: direct Anthropic SDK
    try:
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=_SYSTEM_PROMPT,
            messages=cast("list[MessageParam]", messages),
        )
        block = response.content[0] if response.content else None
        return block.text if block and hasattr(block, "text") else ""
    except Exception:
        logger.exception("Failed to generate guidance note")
        return ""


async def _generate_via_router(
    system: str,
    messages: list[dict[str, Any]],
) -> str | None:
    """Try generating via LLM router. Returns None if router unavailable."""
    from src.llm import get_router
    from src.llm.models import LLMTask

    router = get_router()
    if router is None:
        return None

    provider_override = await _get_sandbox_provider_override(router)

    response = await router.complete(
        LLMTask.AGENT,
        messages,
        system=system,
        max_tokens=300,
        provider_override=provider_override,
    )
    return response.text or None


async def _get_sandbox_provider_override(router: Any) -> str | None:
    """Read auto_customer_model from Redis LLM config for sandbox provider override."""
    try:
        import json

        from redis.asyncio import Redis

        from src.config import get_settings as _get_settings
        from src.llm.router import REDIS_CONFIG_KEY

        _settings = _get_settings()
        _redis = Redis.from_url(_settings.redis.url, decode_responses=True)
        try:
            raw = await _redis.get(REDIS_CONFIG_KEY)
            if raw:
                llm_cfg = json.loads(raw)
                ac_model = (llm_cfg.get("sandbox") or {}).get("auto_customer_model", "")
                if ac_model and ac_model in router.providers:
                    return ac_model
        finally:
            await _redis.aclose()
    except Exception:
        logger.debug("Failed to read auto_customer_model from Redis", exc_info=True)
    return None
