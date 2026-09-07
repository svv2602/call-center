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

# Whole words that carry nothing but agreement.
_CONFIRM_WORDS: frozenset[str] = frozenset({
    "так", "да", "ок", "окей", "окейно", "okey", "ok", "yes", "ага", "угу",
    "вірно", "верно", "правильно", "точно", "звісно", "звичайно", "конечно",
    "добре", "добро", "хорошо", "гаразд", "підходить", "подходит",
    "згоден", "згодна", "згодні", "согласен", "согласна",
    "давай", "давайте", "запишіть", "записуйте",
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
