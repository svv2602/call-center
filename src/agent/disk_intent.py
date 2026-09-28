"""Wheel (disk) intent in the caller's words, and the redirect to `search_disks`.

Goldset №2 (2026-09-28): «потрібні литі диски шістнадцятий радіус на Шкоду
Октавію» and «диски тринадцятий радіус на Таврію є?» went to
`get_vehicle_tire_sizes` / `search_tires` / `search_knowledge_base` in all four
runs — the prompt rule «диски → одразу search_disks» does not hold under
attention dilution, so the redirect is made by code.

`has_disk_intent` reads one customer utterance in Ukrainian or Russian (a
one-language vocabulary reads the other language as «клієнт промовчав»).
Brake and clutch discs are not wheels; «шини на дисках» / «шини під литі диски»
are about tyres, not a wheel purchase — those phrases are cut out before the
check, so «гальмівні диски і литі диски» still counts.

`DiskToolRedirect` is per-turn state shared by both agent loops
(`StreamingAgentLoop._execute_one_tool` and `LLMAgent._execute_one`): under
`sales_enabled`, while the customer's last utterance asks for wheels and
`search_disks` is offered but not yet called this turn, a tyre-side tool call is
answered with `DISK_TOOL_HINT` instead of being run — once per turn; the second
such call runs (a refusal repeated every round would leave the turn silent).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

DISK_TOOL_HINT = (
    "Клієнт питає про диски — виклич search_disks (діаметр; якщо відоме авто — "
    "марку, модель і рік). get_vehicle_tire_sizes/search_tires — тільки для шин."
)

# Tools the model reaches for instead of `search_disks` (goldset №2).
DISK_REDIRECTED_TOOLS = frozenset(
    {"get_vehicle_tire_sizes", "search_tires", "search_knowledge_base"}
)

# диск / диска / диску / диском / диске / диски / дисків / дисков / дискам /
# дисками / дисках — one stem, UA and RU case endings.
_DISK = r"диск(?:а|у|ом|е|і|и|ів|ов|ам|ами|ах)?"

# Wheel adjectives that name a wheel on their own: литі / литые / литий / литой,
# штамповані / штампованные / штамповка, ковані / кованые, лиття / литьё.
_WHEEL_WORDS = re.compile(
    r"\b(?:" + _DISK + r"|лит(?:і|ий|а|е|их|им|ими|у|ої|ою|ые|ых|ым|ыми|ой|ую|ая|ое)"
    r"|лиття|литье|литво"
    r"|штампован\w*|штамповк\w*"
    r"|кован(?:і|ий|а|е|их|им|ими|у|ої|ою|ые|ых|ым|ыми|ой|ую|ая|ое)"
    r")\b",
    re.IGNORECASE,
)

# Phrases where «диск» is not a wheel the customer wants to buy.
_NOT_WHEEL = re.compile(
    r"\b(?:гальмівн\w*|тормозн\w*)\s+" + _DISK + r"\b"
    r"|\b" + _DISK + r"\s+(?:\w+\s+)?(?:гальм\w*|тормоз\w*|зчеплен\w*|сцеплен\w*)"
    # «шини на дисках», «шини під литі диски», «без дисків» — tyres on rims.
    r"|\b(?:на|без|під|под)\s+(?:\w+\s+)?" + _DISK + r"\b",
    re.IGNORECASE,
)


def has_disk_intent(text: str | None) -> bool:
    """True when the utterance asks about wheels (disks), UA or RU."""
    if not text:
        return False
    low = text.lower().replace("ё", "е")
    low = _NOT_WHEEL.sub(" ", low)
    return _WHEEL_WORDS.search(low) is not None


def last_customer_text(history: list[dict[str, Any]]) -> str:
    """The caller's latest free-text utterance (tool_result turns skipped)."""
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = " ".join(t for t in texts if t.strip())
            if joined:
                return joined
    return ""


class DiskToolRedirect:
    """Per-turn redirect of tyre-side tools to `search_disks`."""

    def __init__(self, *, sales_enabled: bool, tools: Iterable[dict[str, Any]]) -> None:
        self._armed = sales_enabled and any(t.get("name") == "search_disks" for t in tools)
        self._disks_called = False
        self._refused = False

    def note_round(self, tool_names: Iterable[str]) -> None:
        """Record the tools the model asked for in this round, before they run."""
        if "search_disks" in tool_names:
            self._disks_called = True

    def check(self, tool_name: str, history: list[dict[str, Any]]) -> str | None:
        """The refusal text if this call must not run, else None."""
        if not self._armed or self._disks_called or self._refused:
            return None
        if tool_name not in DISK_REDIRECTED_TOOLS:
            return None
        last = last_customer_text(history)
        if not has_disk_intent(last):
            return None
        self._refused = True
        logger.warning("disk_tool_redirect tool=%s last_customer_text=%r", tool_name, last[:120])
        return DISK_TOOL_HINT
