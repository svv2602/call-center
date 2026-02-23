"""Generate guidance notes for conversation patterns via LLM.

Uses unified llm_complete helper (router → Anthropic fallback)
to draft a system prompt instruction from a turn group's dialogue fragment.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.helpers import llm_complete
from src.llm.models import LLMTask

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ти експерт з quality assurance у кол-центрі інтернет-магазину шин (Україна).
На основі фрагмента діалогу (репліки клієнта та агента) сформулюй коротку інструкцію
для системного промпту ІІ-агента.

Інструкція повинна бути:
- Українською мовою (це текст для системного промпту агента, який спілкується українською)
- Короткою (2-4 речення)
- Конкретною — описувати правило поведінки агента
- У форматі директиви: «Агент повинен...» / «При ... агент зобов'язаний...»

НЕ додавай пояснення, вступи чи висновки — тільки саму інструкцію."""


def _build_user_message(
    turns: list[dict[str, Any]],
    intent_label: str,
    pattern_type: str,
    correction: str | None,
) -> str:
    """Build user message for guidance generation."""
    lines = [f"Інтент: {intent_label}", f"Тип: {pattern_type}", "", "Діалог:"]
    for turn in turns:
        speaker = "Клієнт" if turn.get("speaker") == "customer" else "Агент"
        lines.append(f"  {speaker}: {turn.get('content', '')}")

    if pattern_type == "positive":
        lines.append("")
        lines.append("Опиши, що агент робить правильно, і сформулюй правило для промпту.")
    elif pattern_type == "negative":
        lines.append("")
        if correction:
            lines.append(f"Виправлення: {correction}")
        lines.append("Опиши помилку агента і сформулюй правило для її виправлення.")

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

    provider_override = await _get_sandbox_provider_override()

    try:
        return await llm_complete(
            LLMTask.AGENT,
            messages,
            system=_SYSTEM_PROMPT,
            max_tokens=300,
            provider_override=provider_override,
        )
    except Exception:
        logger.exception("Failed to generate guidance note")
        return ""


async def _get_sandbox_provider_override() -> str | None:
    """Read auto_customer_model from Redis LLM config for sandbox provider override."""
    try:
        import json

        from redis.asyncio import Redis

        from src.config import get_settings
        from src.llm import get_router
        from src.llm.router import REDIS_CONFIG_KEY

        router = get_router()
        if router is None:
            return None

        settings = get_settings()
        redis = Redis.from_url(settings.redis.url, decode_responses=True)
        try:
            raw = await redis.get(REDIS_CONFIG_KEY)
            if raw:
                llm_cfg = json.loads(raw)
                ac_model = (llm_cfg.get("sandbox") or {}).get("auto_customer_model", "")
                if ac_model and ac_model in router.providers:
                    return ac_model
        finally:
            await redis.aclose()
    except Exception:
        logger.debug("Failed to read auto_customer_model from Redis", exc_info=True)
    return None
