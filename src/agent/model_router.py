"""LLM model routing based on query complexity.

Routes simple queries to Claude Haiku (cheaper, faster) and
complex queries to Claude Sonnet (more capable).

Expected savings: 30-40% on LLM costs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.monitoring.metrics import call_scenario_total

logger = logging.getLogger(__name__)

# Simple patterns that can be handled by Haiku
SIMPLE_PATTERNS = [
    # Order status check
    r"(статус|де|де мій|відстежити|трекінг|замовлення)\s*(номер|#)?\s*\d*",
    # Availability check
    r"(є в наявності|чи є|наявність|залишки|скільки є)",
    # Transfer to operator
    r"(оператор|людину|менеджер|переключ|з'єднай)",
    # Simple greetings
    r"^(добрий день|привіт|доброго ранку|добрий вечір|здрастуйте)$",
    # Yes/No answers
    r"^(так|ні|добре|гаразд|зрозуміло|дякую)$",
    # Price inquiry
    r"(скільки коштує|ціна|вартість|прайс)\s+",
]

# Complex patterns requiring Sonnet
COMPLEX_PATTERNS = [
    # Consultation / comparison
    r"(порівня|різниця між|що краще|яка різниця|порадь|рекомендуй|підкажіть)",
    # Order creation (needs confirmation logic)
    r"(замов|оформ|купити|придбати|хочу взяти)",
    # Fitting booking
    r"(шиномонтаж|монтаж|запис|записатися|замін)",
    # Technical questions
    r"(характеристик|параметр|індекс навантаження|індекс швидкості|типорозмір)",
    # Multi-step dialogs
    r"(а ще|також|і ще|крім того|додатково)",
]

# Pre-compile patterns
_simple_compiled = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in SIMPLE_PATTERNS]
_complex_compiled = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in COMPLEX_PATTERNS]


class ModelRouter:
    """Routes calls to appropriate LLM model based on complexity."""

    def __init__(
        self,
        haiku_model: str = "claude-haiku-4-5-20251001",
        sonnet_model: str = "claude-sonnet-4-5-20250929",
        enabled: bool = True,
    ) -> None:
        self._haiku_model = haiku_model
        self._sonnet_model = sonnet_model
        self._enabled = enabled

    def select_model(
        self,
        customer_text: str,
        turn_count: int = 0,
        has_active_order: bool = False,
    ) -> str:
        """Select the appropriate model for the current request.

        Args:
            customer_text: The customer's current message.
            turn_count: Number of turns so far in the conversation.
            has_active_order: Whether there's an active order being processed.

        Returns:
            Model identifier string.
        """
        if not self._enabled:
            return self._sonnet_model

        # Complex scenarios always use Sonnet
        if has_active_order:
            return self._sonnet_model

        # Multi-turn conversations tend to be complex
        if turn_count > 4:
            return self._sonnet_model

        # Check for complex patterns first
        for pattern in _complex_compiled:
            if pattern.search(customer_text):
                logger.debug("Complex pattern matched, using Sonnet: %s", customer_text[:50])
                return self._sonnet_model

        # Check for simple patterns
        for pattern in _simple_compiled:
            if pattern.search(customer_text):
                logger.debug("Simple pattern matched, using Haiku: %s", customer_text[:50])
                return self._haiku_model

        # Default to Sonnet for unrecognized patterns
        return self._sonnet_model

    def classify_scenario(self, customer_text: str) -> str:
        """Classify the customer's request into a scenario category.

        Used for metrics tracking.
        """
        text_lower = customer_text.lower()

        if any(w in text_lower for w in ["замов", "оформ", "купити"]):
            return "order"
        if any(w in text_lower for w in ["монтаж", "шиномонтаж", "запис"]):
            return "fitting"
        if any(w in text_lower for w in ["наявн", "є в", "залишк"]):
            return "availability"
        if any(w in text_lower for w in ["порівн", "різниц", "порад", "рекомен"]):
            return "consultation"
        if any(w in text_lower for w in ["статус", "де мій", "трекінг"]):
            return "order_status"
        if any(w in text_lower for w in ["шин", "колес", "гум", "розмір"]):
            return "tire_search"

        return "other"
