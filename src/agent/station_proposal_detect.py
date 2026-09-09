"""Did the bot just propose one specific fitting station?

Wave 6-H (2026-09-09). The FSM charges «так» as a `parser_null` even when the
bot's previous turn named a station and the caller was plainly agreeing to it.
Resolving that exchange needs two independent facts, and this module owns the
first one: *the bot asked a station-choice question*.

Why a marker set and not «the bot's turn mentions a station»
------------------------------------------------------------
Because the second reading is a hole. Measured over 42 calls, «the bot named
exactly one station from the snapshot and the caller agreed» fires 22 times,
and most of the surplus is the Krok 8 summary («перевіримо: 9 вересня о 17:00,
м. Дніпро, вул. Княгині Ольги, 24»), where the station is already chosen. A
marker that means «choose a station» excludes that by construction.

Why the search stops at the first turn that asked something else
----------------------------------------------------------------
The markers alone are *not* enough, and call ``bd95036c`` is the proof. At t10
the bot asks «Для зміни міста потрібне підтвердження. Підтвердіть, будь ласка,
що замінюємо місто на Черкаси» and the caller says «підтверджую» — but two
turns back, at t7, sits «…Продовжимо запис на цю точку?», which *is* a marker.
A flat two-turn window reaches over the city-change prompt, fires, and pins the
Дніпро station to a caller who just asked to leave Дніпро.

So the window is not flat. Scanning newest-first, a turn that carries no marker
ends the search **unless it is a re-ask** — one of the three shapes the bot uses
when it got no answer at all («Перепрошую, не розчула…», «Я на зв'язку…», «Ви ще
на лінії?», 33 occurrences in the corpus and no others). Crossing a re-ask is
sound because a re-ask repeats the pending question rather than replacing it;
crossing anything else means treating a stale question as if it were live.

That ordering matters for where the safety lives. `station_parser` also narrows
by the already-chosen city, and on ``bd95036c`` that narrowing does refuse —
but only because `compound_parse` happened to read «Черкасах» out of the
caller's turn. Had the caller named a landmark instead, `city` would be empty,
the narrowing would be a no-op, and the wrong address would be pinned. The
re-ask rule closes it without depending on any of that.

Why the markers are whole phrases
---------------------------------
The bare stem «записуємо» is the trap. In the same corpus it also opens «У
якому місті **записуємо** шиномонтаж?» (a *city* question) and «**Записуємо** на
9 вересня?» (a *date* question). Both would hand the FSM a station the caller
never chose. Every marker here is therefore a phrase harvested from real bot
turns, not a stem that seemed suggestive.

Note the asymmetry «знайшла точку» / «точки не знайшла»: the failure line («За
орієнтиром "на природе" точки не знайшла») uses the genitive after a negation
and does not contain the accusative phrase, so it is excluded without a negation
rule of its own.

This module answers only «was a station being proposed». *Which* station, and
whether the snapshot resolves it unambiguously, stays in `station_parser` —
where the matching rules already live and must not be copied.
"""

from __future__ import annotations

#: Harvested from the bot turns that actually precede a caller agreement in the
#: 2026-09-07..09 corpus. Ordered by observed frequency; «записуємо туди» alone
#: covers 10 of the exchanges.
_PROPOSE_MARKERS: tuple[str, ...] = (
    "записуємо туди",
    "знайшла точку",
    "знайшов точку",
    "маю точку",
    "записуємо у",
    "записуємо на монтаж",
    "обрана точка",
    "запис на цю точку",
)

#: The bot's «I got no answer» turns, harvested the same way: every bot turn in
#: the corpus that directly follows another bot turn is one of exactly these
#: three shapes (26 + 6 + 1 = 33 occurrences, no others). They repeat the
#: pending question instead of asking a new one, which is what makes them safe
#: to look past.
_REASK_MARKERS: tuple[str, ...] = (
    "перепрошую, не розчула",
    "я на зв'язку",
    "ви ще на лінії",
)

_PUNCT_MAP = str.maketrans({"ʼ": "'", "’": "'", "`": "'", " ": " "})


def _normalize(text: str) -> str:
    """Lowercase, fold apostrophes, collapse whitespace.

    Deliberately does *not* strip punctuation: the markers are multi-word
    phrases and collapsing «вул.» into «вул» would let a marker straddle a
    sentence boundary it never crossed in the transcript.
    """
    return " ".join(text.translate(_PUNCT_MAP).lower().split())


def proposed_station(bot_utterances: list[str]) -> bool:
    """True when the bot's *pending* question proposes a specific station.

    Pass the most recent assistant turns **newest first**. Scanning stops at the
    first turn that carries no proposal marker and is not a re-ask, so the
    caller's «так» is only ever read against a question that is still open. A
    flat window would answer a question the bot had already moved on from — see
    the module docstring for the `bd95036c` case that made this necessary.

    Looking past a re-ask is what the second slot is for: on call `2536a21d` the
    bot asks «Знайшла точку на вул. Героїв Дніпра, 7 у Черкасах. Записуємо
    туди?», gets silence, re-asks, and only then hears «так».
    """
    for utterance in bot_utterances:
        if not utterance:
            continue
        normalized = _normalize(utterance)
        if any(marker in normalized for marker in _PROPOSE_MARKERS):
            return True
        if not any(marker in normalized for marker in _REASK_MARKERS):
            return False
    return False
