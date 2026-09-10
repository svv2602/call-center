"""Did the customer just confirm the Krok 8 booking summary?

Wave 15 (2026-09-07). The Wave 5 «EMERGENCY book_fitting» banner only fires
when the pipeline recognises both sides of the confirmation exchange. On call
``7462c08b`` it recognised neither, and the LLM answered «Наталя, ви записані.
СМС підтвердження надійде.» without ever calling ``book_fitting``:

* the customer said «так підтверджує» — the old whole-string regex accepted a
  single affirmation word and nothing else, so a two-word answer missed;
* the bot's *last* turn was the re-ask «Скажіть, будь ласка, "так" щоб
  підтвердити…», which does not contain «Підтверджуєте», so the marker missed
  even though the pending question was unchanged.

Both detectors below are deliberately generous. They only ever *enable* a
banner that forces the tool call the flow already requires, and the caller
additionally gates on every checklist field being filled — so a false positive
books what the customer just approved, while a false negative silently lets the
bot lie about a booking that does not exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Whole words that carry nothing but agreement.
_CONFIRM_WORDS: frozenset[str] = frozenset({
    "так", "да", "ок", "окей", "окейно", "okey", "ok", "yes", "ага", "угу",
    "вірно", "верно", "правильно", "точно", "звісно", "звичайно", "конечно",
    "добре", "добро", "хорошо", "гаразд", "підходить", "подходит",
    "згоден", "згодна", "згодні", "согласен", "согласна",
    "давай", "давайте", "запишіть", "записуйте", "записуємо",
    "воно", "таки", "таково",
})

# Prefixes — «підтверджую», «підтверджує», «підтверджено», «підтверджаю»…
# STT routinely picks the wrong person/aspect ending, so match the stem.
_CONFIRM_STEMS: tuple[str, ...] = (
    "підтвердж", "підтверди", "подтвержд", "подтверди", "погодж", "соглас",
)

# An answer this long is no longer a bare «yes» — it carries new information
# («так, але давайте на пʼятницю»), and must reach the LLM unmodified.
_MAX_CONFIRM_TOKENS = 4

# Bot phrasings that leave a Krok 8 confirmation question open. «підтверд» also
# covers the «не розчула» re-ask («…"так" щоб підтвердити або "ні" щоб
# змінити»), which is what call 7462c08b tripped over.
_ASK_MARKERS: tuple[str, ...] = (
    "підтверджуєте", "підтверджує", "підтвердити", "підтверджен",
    "перевіримо:", "підтверджуєш",
)

# Bot phrasings that invite a bare «так» *outside* Krok 8. The FSM never asks
# these — they belong to the LLM's own checklist — but the caller answering one
# lands in whatever state the machine happens to be sitting in.
#
# An allow-list, not a heuristic on «?»: every other question the bot asks is
# open («Яка марка авто?») or two-option («…свої з собою чи ті, що у нас на
# зберіганні?»), and a «так» to one of those really is a non-answer.
# «записуємо туди» is listed with its object for that reason — «На яку дату
# записуємо?» is the second most common question in the log and is not a
# yes/no.
_YES_NO_ASK_MARKERS: tuple[str, ...] = (
    "записуємо туди",
    "записуємо сюди",
    "вірно?",
    "правильно?",
    "правильно розумію",
    "підходить?",
    "ви ще на лінії",
    "ви маєте на увазі",
)

# …unless the same sentence also offers a choice. `fe1857ba` (2026-09-10) is
# why: the caller said «так» to «Шини привозите свої з собою чи ті, що у нас на
# зберіганні?», which answers nothing, and it has to keep costing an attempt.
# The marker list already excludes that question; this makes it hold when the
# LLM rephrases one of the listed questions into a choice.
_CHOICE_MARKER = " чи "

_PUNCT = " \t\n.,!?;:—–-\"'«»()"


def _tokens(text: str) -> list[str]:
    normalized = text.lower().replace("ʼ", "'").replace("’", "'").replace("`", "'")
    return [t for t in (w.strip(_PUNCT) for w in normalized.split()) if t]


def _is_confirm_token(token: str) -> bool:
    return token in _CONFIRM_WORDS or token.startswith(_CONFIRM_STEMS)


def is_confirmation(customer_text: str) -> bool:
    """True when the utterance is pure agreement and carries nothing else."""
    tokens = _tokens(customer_text)
    if not tokens or len(tokens) > _MAX_CONFIRM_TOKENS:
        return False
    return all(_is_confirm_token(t) for t in tokens)


def booking_was_confirmed(turns: Sequence[tuple[str, str]]) -> bool:
    """Did the exchange just before this point close a Krok 8 confirmation?

    `turns` is the dialog oldest-first as (speaker, content); "user" is the
    customer. Looks at the newest customer turn and the two bot turns before
    it — the same window the pipeline's EMERGENCY banner uses, so a gate built
    on this reads the state exactly as the banner does.
    """
    bot_before_answer: list[str] = []
    customer_answer = ""
    for speaker, content in reversed(turns):
        if not content:
            continue
        if not customer_answer:
            if speaker == "user":
                customer_answer = content.strip()
            continue
        if speaker == "assistant":
            bot_before_answer.append(content)
            if len(bot_before_answer) == 2:
                break
    return asked_for_confirmation(bot_before_answer) and is_confirmation(customer_answer)


def is_yes_no_question(bot_utterance: str) -> bool:
    """Did the bot's last turn ask something a bare «так» actually answers?

    Krok 8 counts — `_ASK_MARKERS` is included — but so do the confirmations
    the LLM scatters through the rest of the flow, which is the point. The FSM
    charges a failed answer to the state it is *in*, and the bot regularly asks
    a confirmation belonging to some other step:

    * `fe1857ba`, `cf43d623` (2026-09-10): «Записуємо туди?» → «так» /
      «записуємо». The FSM sat in STATION, where `station_id` is what it wanted,
      so a valid answer spent an attempt. Both calls ran the budget out and
      reached an operator.
    * `b034315e` (2026-09-10): «Пропоную понеділок, чотирнадцяте вересня.
      Підходить?» → «так», charged to TIME.

    Deliberately narrow — an allow-list of phrasings taken off the log, vetoed
    by « чи ». A wrong «yes» here does not book anything; it withholds one
    escalation tick, which is why the veto matters more than the coverage.
    """
    low = (bot_utterance or "").lower()
    if not low or _CHOICE_MARKER in low:
        return False
    return any(marker in low for marker in (*_YES_NO_ASK_MARKERS, *_ASK_MARKERS))


def asked_for_confirmation(bot_utterances: list[str]) -> bool:
    """True when one of the bot's recent turns left a Krok 8 question open.

    Pass the most recent assistant turns newest-first; a filler re-ask
    («Перепрошую, не розчула…») sits between the question and the answer often
    enough that looking only at the latest turn misses the exchange.
    """
    for utterance in bot_utterances:
        if not utterance:
            continue
        low = utterance.lower()
        if any(marker in low for marker in _ASK_MARKERS):
            return True
    return False
