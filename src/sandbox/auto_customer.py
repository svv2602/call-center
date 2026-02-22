"""Auto-generate customer responses for sandbox testing.

Uses LLM router (with fallback providers) or direct Anthropic SDK
to simulate customer behavior in various personas.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import anthropic

from src.config import get_settings

if TYPE_CHECKING:
    from anthropic.types import MessageParam

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

    # Try LLM router first (supports Gemini, OpenAI, etc.), fall back to direct Anthropic SDK
    try:
        reply = await _generate_via_router(system, messages)
        if reply:
            return reply
    except Exception:
        logger.debug("LLM router not available for auto-customer, trying direct SDK")

    try:
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic.api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system,
            messages=cast("list[MessageParam]", messages),
        )
        block = response.content[0] if response.content else None
        return block.text if block and hasattr(block, "text") else ""
    except Exception:
        logger.exception("Failed to generate customer reply")
        return "Так, добре."


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

    response = await router.complete(
        LLMTask.AGENT,
        messages,
        system=system,
        max_tokens=200,
    )
    return response.text or None
