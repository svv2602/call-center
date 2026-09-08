"""`brand_parser` — BRAND. Curated list synchronously, 14 561 aliases on demand.

`parse()` is `preparse.preparse_fitting()`, the same curated whole-word list
the broad pass uses. It deliberately contains STT mutations («билл» → BYD,
«лікер» → Zeekr), which is why a hit is `0.9` and not a literal `1.0`.

`aresolve()` is the Wave 8 alias table (`e67c6d7`): «Дастер» and «Тігуан» are
models, not brands, and are not in the curated list at all. Resolution is a DB
round-trip, so it can only run in `live` and only when the context carries a
connection — in `shadow` the parser is the curated list and nothing more.

Lookup is exact-match on the normalised alias (`vehicle_translit.normalize_alias`
runs inside `resolve_by_alias`), so the candidate has to be the word itself.
The whole utterance is tried first — an answer to «Яка марка вашого авто?» is
usually one or two words — then the individual words, longest first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.agent.compound_parse import _normalize
from src.agent.parsers.base import NOT_MENTIONED, ParseOutcome, graded
from src.agent.preparse import preparse_fitting

if TYPE_CHECKING:
    from src.agent.parsers.base import ParseContext

logger = logging.getLogger(__name__)

#: Curated list — solid, but it holds STT mutations on purpose.
_CURATED_CONFIDENCE = 0.9
#: Alias table hit. Same level: the table is auto-imported and transliterated.
_ALIAS_CONFIDENCE = 0.9

#: Shortest word worth a round-trip. «БМВ» is three characters.
_MIN_CANDIDATE_LEN = 3
#: Upper bound on DB round-trips for one turn.
_MAX_CANDIDATES = 6


def _candidates(customer_text: str) -> list[str]:
    """Whole utterance first, then its longest words."""
    text = _normalize(customer_text).strip()
    if not text:
        return []
    words = [w.strip(".,!?;:«»\"'()") for w in text.split()]
    words = [w for w in words if len(w) >= _MIN_CANDIDATE_LEN]
    words.sort(key=len, reverse=True)
    ordered = [text, *words]
    seen: set[str] = set()
    unique = [c for c in ordered if not (c in seen or seen.add(c))]
    return unique[:_MAX_CANDIDATES]


async def _aresolve_brand(ctx: ParseContext, outcome: ParseOutcome) -> ParseOutcome:
    """Resolve a rare brand through the alias table.

    Exceptions are **not** caught here. Per §3.2 rule 4 the engine logs the
    failure at WARNING with its traceback and continues with the `parse()`
    result; swallowing it at this level would hide a broken DB behind a field
    that merely «did not detect anything» (`37fb2d0`).

    The lookup module is imported here rather than at module scope: it pulls in
    SQLAlchemy, and `src.agent.parsers` must stay importable without the DB
    stack — the same reason `src/core/pipeline.py` defers its detector imports.
    """
    from src.agent.vehicle_alias_lookup import resolve_by_alias

    if ctx.conn is None:
        logger.debug("brand_parser.aresolve: no connection in context — skipped")
        return outcome

    for candidate in _candidates(ctx.customer_text):
        result = await resolve_by_alias(ctx.conn, candidate)
        if result.ambiguous:
            # Several brands share the alias — the caller has to disambiguate.
            logger.debug("brand_parser.aresolve: %r is ambiguous — leaving unresolved", candidate)
            continue
        if result.brand_name:
            return graded(result.brand_name, _ALIAS_CONFIDENCE)
    return outcome


class BrandParser:
    """BRAND, plus an optional alias-table resolution."""

    name = "brand_parser"
    field_name = "brand"
    aresolve = staticmethod(_aresolve_brand)

    def parse(self, ctx: ParseContext) -> ParseOutcome:
        text = (ctx.customer_text or "").strip()
        if not text:
            return NOT_MENTIONED
        brand = preparse_fitting(text).get("brand")
        if not brand:
            return NOT_MENTIONED
        return graded(brand, _CURATED_CONFIDENCE)


PARSER = BrandParser()
