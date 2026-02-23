"""Auto-generate customer responses for sandbox testing.

Uses unified llm_complete helper (router → Anthropic fallback)
to simulate customer behavior in various personas.
"""

from __future__ import annotations

import logging
from typing import Any

from src.llm.helpers import llm_complete
from src.llm.models import LLMTask

logger = logging.getLogger(__name__)

PERSONA_PROMPTS = {
    "neutral": "Ти звичайний клієнт шинного магазину. Відповідай природно українською.",
    "impatient": "Ти нетерплячий клієнт. Поспішаєш, хочеш швидких відповідей. Відповідай коротко, іноді перебиваєш.",
    "confused": "Ти клієнт, який погано розбирається в шинах. Задаєш уточнюючі питання, плутаєш терміни.",
    "angry": "Ти незадоволений клієнт. Скаржишся, вимагаєш менеджера, але можеш заспокоїтися при хорошому обслуговуванні.",
    "expert": "Ти досвідчений автомобіліст, розбираєшся в шинах. Використовуєш технічні терміни, порівнюєш бренди.",
    "rushed": "Ти клієнт, який дуже поспішає. Відповідаєш максимально коротко, хочеш одразу результат без зайвих питань.",
    "detailed": "Ти дотошний клієнт. Цікавишся деталями, просиш порівняння, уточнюєш характеристики, бюджет, умови експлуатації.",
}

_SYSTEM_PROMPT = """Ти імітуєш клієнта інтернет-магазину шин в Україні.
Відповідай ТІЛЬКИ українською мовою. Одне-два речення максимум.
Не використовуй маркери списку чи форматування.
{persona}
{context}"""


async def generate_customer_reply(
    conversation_history: list[dict[str, Any]],
    persona: str = "neutral",
    context_hint: str | None = None,
) -> str:
    """Generate a simulated customer reply based on conversation history.

    Args:
        conversation_history: Anthropic-format conversation history.
        persona: Customer persona type.
        context_hint: Optional hint about what the customer wants to do next.

    Returns:
        Generated customer message text.
    """
    persona_text = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS["neutral"])
    context_text = f"Контекст: {context_hint}" if context_hint else ""

    system = _SYSTEM_PROMPT.format(persona=persona_text, context=context_text)

    # Convert conversation history to customer perspective
    # (swap roles: agent messages become "assistant" from customer's view)
    messages = []
    for msg in conversation_history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Extract text from content blocks
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = " ".join(texts)
        if role == "assistant":
            messages.append({"role": "user", "content": content})
        elif role == "user":
            # Skip tool_result messages
            if isinstance(msg.get("content"), list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in msg["content"]
            ):
                continue
            messages.append({"role": "assistant", "content": content})

    # Add a prompt for the next customer message
    if messages and messages[-1]["role"] == "assistant":
        pass  # Good — last message is from "agent" (mapped as assistant)
    elif not messages:
        # Empty conversation — generate opening message
        messages = [
            {"role": "user", "content": "Привіт, я менеджер шинного магазину. Чим можу допомогти?"}
        ]

    provider_override = await _get_sandbox_provider_override()

    try:
        return await llm_complete(
            LLMTask.AGENT,
            messages,
            system=system,
            max_tokens=200,
            provider_override=provider_override,
        )
    except Exception:
        logger.exception("Failed to generate customer reply")
        return "Так, добре."


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
