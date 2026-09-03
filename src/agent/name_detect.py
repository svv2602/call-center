"""Customer name auto-detection from Krok 0 answer.

Wave 6 (2026-09-03) — call fcfb26a9 turn 1 «Юра» → LLM did not call
`update_customer_profile(name=…)` → session.fitting_customer_name stayed
None → state guard rendered ⏳ Ім'я for the whole 13-min conversation →
LLM invented «Марина» (bot's own name) and later «Василь» (name from a
prompt anti-pattern example) in book_fitting args.

Backend auto-persist parallel to color/storage detection. Only triggers
when bot's last utterance is the Krok 0 name question, so «Юра»
mentioned incidentally later in the call is not misclassified as a
name-change.
"""

from __future__ import annotations

import re
import unicodedata

# Bot phrases that indicate the Krok 0 name question. Substring match on
# the bot's LAST utterance (accents stripped, lowercased).
_NAME_QUESTION_MARKERS: tuple[str, ...] = (
    "до вас звертатися",
    "до вас звертат",
    "як вас звати",
    "як вас зват",
    "як вас можна називати",
    "не розчула ім",
    "назвіть, будь ласка, як",
)

# Words that look like names but aren't (frequent STT noise + fitting
# vocabulary that could show up as a 1-word reply on Krok 0).
_NAME_STOP_WORDS: frozenset[str] = frozenset(
    {
        "алло", "ало", "але",
        "так", "да", "ні", "нет", "ок", "окей",
        "добре", "дякую", "спасибо",
        "мгм", "угу", "ага",
        "нічого", "ничего",
        "шиномонтаж", "монтаж", "запис",
        "київ", "дніпро", "харків", "черкаси", "запоріжжя",
        "львів", "одеса", "полтава", "суми",
        "марка", "модель", "колір",
        "оператор", "менеджер", "консультант",
        "перепрошую",
        "варкис", "воздух", "скажи",
        "марина",  # bot's own name — never accept as customer name
    }
)

# One capitalized Cyrillic word, optionally followed by a second (for
# two-word names like «Ганна Іванівна»). Length 2-20 chars per word.
_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*([А-ЯЁІЇЄҐ][а-яёіїєґ'\-]{1,19})"
    r"(?:\s+([А-ЯЁІЇЄҐ][а-яёіїєґ'\-]{1,19}))?"
    r"\s*[,.\!?]*\s*$"
)

# STT sometimes returns lowercase — apply a laxer pattern that requires
# no capital but still checks for realistic length + Cyrillic-only.
_NAME_PATTERN_LOWER: re.Pattern[str] = re.compile(
    r"^\s*([а-яёіїєґ'\-]{2,20})"
    r"(?:\s+([а-яёіїєґ'\-]{2,20}))?"
    r"\s*[,.\!?]*\s*$"
)


def _strip_accents(text: str) -> str:
    """Remove combining accent marks. TTS input uses «зверта́тися» with a
    U+0301 combining acute for stress; our markers store plain text."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def is_name_question(bot_utterance: str) -> bool:
    """True if bot's last utterance is asking for the customer name."""
    if not bot_utterance:
        return False
    lower = _strip_accents(bot_utterance).lower()
    return any(marker in lower for marker in _NAME_QUESTION_MARKERS)


def detect_name(customer_text: str) -> str | None:
    """Extract a customer name from a short answer to Krok 0.

    Returns the name capitalized (first-name only) or None if the text
    doesn't look like a name. Ukrainian tire shops address customers by
    first name — patronymic/surname are discarded.
    """
    if not customer_text:
        return None

    stripped = customer_text.strip()
    if not stripped:
        return None

    # Long responses are sentences («я хочу дізнатися вартість»), not names.
    if len(stripped) > 30:
        return None

    words = stripped.split()
    if len(words) > 2:
        return None

    # Stop-word check against the lowercase form.
    lower_all = stripped.lower().rstrip(".,!?")
    if lower_all in _NAME_STOP_WORDS:
        return None
    if any(w.lower().rstrip(".,!?") in _NAME_STOP_WORDS for w in words):
        return None

    match = _NAME_PATTERN.match(stripped)
    if not match:
        match = _NAME_PATTERN_LOWER.match(stripped)
        if not match:
            return None

    first = match.group(1)
    return first.capitalize()
