"""Has a caller who asked the price agreed to book?

A price consultation ends in an offer (prompt «Крок Ц-5: Записуємо на
монтаж?»), and the booking checklist starts only after a yes. The LLM skips the
offer when the quote itself closes on a question: on 2026-09-24 the quote ended
«У вас легковий чи позашляховик?», the caller said «позашляховик», and the bot
went straight to «Шини привозите свої з собою…» — a booking nobody asked for.

Pure functions over the dialog, oldest first, as (speaker, content) with
"user" for the caller and "assistant" for the bot.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.agent.confirm_detect import is_confirmation

if TYPE_CHECKING:
    from collections.abc import Sequence

BOOKING_OFFER = "Записати вас на шиномонтаж?"

#: The caller's own words asking to book. «запис» covers «записатися»,
#: «запись», «записаться»; «запиш» covers «запишіть», «запишите».
_CALLER_BOOKING = re.compile(r"запис|запиш")

#: A bot sentence offering to book, which a bare «так» then accepts.
_BOT_OFFER = re.compile(r"запис(?:ати|уємо|уємось|атися|ую)\b[^?]*\?|записуємо\?")


def caller_agreed_to_book(turns: Sequence[tuple[str, str]]) -> bool:
    """True once the caller asked to book, or said yes to the bot's offer."""
    previous_bot = ""
    for speaker, content in turns:
        text = (content or "").lower()
        if speaker == "assistant":
            previous_bot = text
            continue
        if speaker != "user":
            continue
        if _CALLER_BOOKING.search(text):
            return True
        if previous_bot and _BOT_OFFER.search(previous_bot) and is_confirmation(text):
            return True
    return False


def offers_spoken(turns: Sequence[tuple[str, str]]) -> int:
    """How many times the gate's offer has been said on this call."""
    return sum(
        1 for speaker, content in turns if speaker == "assistant" and BOOKING_OFFER in content
    )
