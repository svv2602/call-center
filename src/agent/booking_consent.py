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

#: What a booking question becomes once the caller has said no to the offer.
#: Carries a `_FAREWELL_MARKERS` phrase, so the pipeline's hangup grace applies.
BOOKING_DECLINED_FAREWELL = "Добре. Якщо буде потрібно — звертайтесь. Гарного дня!"

#: Gate modes, decided per turn by the pipeline.
GATE_OFFER = "offer"
GATE_FAREWELL = "farewell"

#: The caller's own words asking to book. «запис» covers «записатися»,
#: «запись», «записаться»; «запиш» covers «запишіть», «запишите».
_CALLER_BOOKING = re.compile(r"запис|запиш")

#: A bot sentence offering to book, which a bare «так» then accepts. No comma
#: between the verb and the question mark: the greeting menu «Ви бажаєте
#: записатися на шиномонтаж, дізнатися вартість, скасувати…?» is not an offer,
#: and a «так» to it says nothing about booking.
_BOT_OFFER = re.compile(r"запис(?:ати|уємо|уємось|атися|ую)\b[^?,]*\?")


def is_booking_offer(text: str) -> bool:
    """True when this bot sentence offers to book («Записуємо на шиномонтаж?»)."""
    return bool(_BOT_OFFER.search((text or "").lower()))


def _offer_answers(turns: Sequence[tuple[str, str]]) -> list[str]:
    """How the caller answered each booking offer, oldest first: yes / no / unclear."""
    from src.agent.interrupts import _yes_no

    answers: list[str] = []
    previous_bot = ""
    for speaker, content in turns:
        text = (content or "").lower()
        if speaker == "assistant":
            previous_bot = text
            continue
        if speaker != "user" or not previous_bot or not _BOT_OFFER.search(previous_bot):
            continue
        answers.append("yes" if is_confirmation(text) else _yes_no(text))
        previous_bot = ""
    return answers


def caller_agreed_to_book(turns: Sequence[tuple[str, str]]) -> bool:
    """True once the caller asked to book, or said yes to the bot's offer."""
    if any(
        speaker == "user" and _CALLER_BOOKING.search((content or "").lower())
        for speaker, content in turns
    ):
        return True
    return "yes" in _offer_answers(turns)


def caller_declined_booking(turns: Sequence[tuple[str, str]]) -> bool:
    """True when the caller's latest clear answer to a booking offer was no.

    Call 354ff3b5 (2026-09-25): «Бажаєте записатися?» → «Ні дякую» → the LLM
    asked «Шини привозите свої…» anyway.
    """
    answers = [a for a in _offer_answers(turns) if a != "unclear"]
    return bool(answers) and answers[-1] == "no"


def offers_spoken(turns: Sequence[tuple[str, str]]) -> int:
    """How many booking offers the bot has made on this call, by anyone."""
    return sum(
        1
        for speaker, content in turns
        if speaker == "assistant" and _BOT_OFFER.search((content or "").lower())
    )


def gate_mode(turns: Sequence[tuple[str, str]]) -> str | None:
    """What a booking question should become this turn, after a price quote.

    None once the caller has agreed — the booking is theirs now. Otherwise a
    farewell if their last word on an offer was no, else the offer itself, at
    most twice: a caller who lets two offers pass is steering elsewhere.
    """
    if caller_agreed_to_book(turns):
        return None
    if caller_declined_booking(turns):
        return GATE_FAREWELL
    return GATE_OFFER if offers_spoken(turns) < 2 else None
