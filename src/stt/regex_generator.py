"""LLM-assisted regex builder for STT correction rules.

The content-manager types what they *heard* (bad_token) and what the
customer *meant* (replacement). This module asks an LLM to build a
regex covering the relevant morphological variants for Ukrainian +
Russian without over-generalising into unrelated words.

Rationale
---------
A one-liner like ``\\bприятный\\b → пʼятницю`` only fixes one form.
"приятная", "приятного", "приятные" all leak through and surface as
separate suggestions later. The LLM sees the sample transcripts and
picks between two strategies:

  * Narrow  — when samples share a fixed prefix ("на приятный",
    "на приятный трюм") → ``\\bна\\s+приятн\\w+`` with replacement
    "на пʼятницю". Lowest false-positive risk.
  * Broad   — when samples vary widely → ``\\b(приятн(ый|ая|ые|ого|ому|ую))\\b``
    covering the paradigm.

The LLM output is validated with re.compile before returning to the
API. On any failure (LLM error, invalid regex, malformed JSON) the
function falls back to the deterministic ``\\bTOKEN\\b`` — the same
default the scanner produces, so the caller always gets a usable
pattern.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You build Python regex patterns that fix reproducible STT (speech-to-text) errors for a Ukrainian call-centre bot. The bot serves callers who speak Ukrainian OR Russian. Google STT sometimes hears one word as another — the manager tells you what was heard and what was meant, you produce a regex.

## Rules (strict)

1. **Always use `\\b` word boundaries** around matched forms — only at the very START and very END of the whole pattern, never in the middle of a multi-fragment bad token.
2. **Cover morphological forms of the source noun/adjective/verb** when it's a real Ukrainian/Russian word. For "приятный" also match "приятная", "приятного", "приятную", "приятные", "приятненький". For "виктора" also match "викторе", "викторов", "викторовна".
3. **Use `\\w+` sparingly** — only when the samples show variable endings AND you can't enumerate them cleanly. Prefer `(форма1|форма2|форма3)` explicit alternation.
4. **DO NOT over-generalise**. If samples all say "на X", make the regex require the "на " prefix. If samples show different prefixes, drop the prefix requirement.
5. **Case-insensitive flag `i` is applied by the runtime** — do NOT emit `(?i)` in the pattern, and don't add capital-letter alternatives.
6. **Replacement is literal** — no backreferences unless you also captured a group you want to preserve (rare — usually the prefix/suffix is discarded).
7. **Never invent samples**. Only use what's provided.
8. **Multiple heard variants** — when the manager gives >1 bad token (e.g. "тёма", "сёма", "тема" all meant "сьома"), produce ONE regex combining them via non-capturing alternation `\\b(?:вариант1|вариант2|вариант3)\\b`. Extend each branch with the appropriate morphological tail per rule #2 — share a common suffix outside the alternation where possible (e.g. `\\b(?:тьом|сьом|тем)\\w*\\b` when all variants share the ending). Do NOT emit separate patterns.

9. **Skeleton compression (preferred over full-word alternation)** — when 2+ heard variants share a common length-aligned STRUCTURE but differ at a few fixed positions, prefer a compressed pattern with character classes + optionals at those positions instead of listing whole words in alternation. This catches nearby STT hallucinations the manager didn't type. Compress ONLY positions where variation was actually observed — do NOT invent variability.

   Method:
     (a) Align variants char-by-char. If lengths differ only in the tail, handle the tail via a small trailing alternation.
     (b) For each varying position, collect the observed characters into a `[...]` bracket. If a position is absent in some variants (e.g. optional soft sign `ь`), use `?` — `сь?`, `нн?`.
     (c) Apply Cyrillic UA/RU alternation brackets (`[іи]`, `[єе]`, `[ьъ]`) at the same positions if the pattern would benefit.
     (d) Wrap the tail differences in `(?:тail1|tail2|tail3)` when they're too dissimilar for a bracket class.

   Example: bad_tokens=["катрузаводської", "патрозоводская", "катрозаводська"], meant="Петрозаводська"
     Align → pos1={к,п,к}, pos5={у,о,о}, pos7={а,о,а}, pos12={ь present, absent, ь present}, tail={ої,ая,а}
     → `\\b[кп]атр[уо]з[ао]водсь?к(?:ої|ая|а)\\b`
     NOT: `\\b(?:катрузаводської|патрозоводская|катрозаводська)\\b` — too narrow, misses realistic hybrids like "катрозаводская".

   When to KEEP full-word alternation instead of compressing:
     * Variants have different roots or are semantically-unrelated guesses (e.g. "тёма"/"сёма"/"тема" — different first letters AND different phonetic identity, not just STT letter-swap). Use rule #8's `(?:...)` alternation.
     * Only 1 variant supplied — nothing to compress.
     * Compressed form would over-generalise to real words (sanity check: would this pattern also match a common Russian/Ukrainian word unrelated to the target? If yes, keep the word alternation).

   When you compress, the `matched_forms` list should include the input variants PLUS 2-3 realistic hybrids the pattern now catches — this helps the reviewer see the widened coverage.

## STT-specific artifact: space injection

When `bad_token` CONTAINS SPACES, Google STT has fragmented a single word into pieces. In this case you MUST:

  (a) Insert `\\s*` between every fragment (not `\\s+` — the fused form also occurs).
  (b) Place `\\b` only at the very start and very end of the whole expression.
  (c) Apply Cyrillic alternation rules (see below) at fragment boundaries.
  (d) Put the ENTIRE expression inside a non-capturing group: `(?:...)`.

  Example: bad_token="один над цать", replacement="одинадцять"
  → `\\b(?:один\\s*н?\\s*а[дт]\\s*ц[ая]?т[ьъ]?(?:и|ю|ма)?)\\b`  — matches all of:
    "один над цать", "одиннадцать", "одинадцять", "один надцять", "одинадцяти", "одиннадцати"

  Key insight for -надцять words: the optional `н?` must sit BETWEEN `один` and `а[дт]`, not at
  the end of `один`. This is because Ukrainian "одинадцять" = один+адцять (no double-н), while
  Russian "одиннадцать" = один+нн+адцать. Pattern `один\\s*н?\\s*а[дт]` handles both.

## STT-specific artifact: Cyrillic Ukrainian/Russian alternation

Google STT trained on Russian often substitutes Russian letters for Ukrainian ones. Apply these substitutions when the bad_token or replacement shows a known Cyrillic mismatch:

  | Ukrainian | Russian | Regex bracket |
  |-----------|---------|---------------|
  | і         | и       | `[іи]`        |
  | є         | е       | `[єе]`        |
  | ь (soft)  | ъ (hard)| `[ьъ]`        |
  | single н  | double нн (Russian geminate) | `нн?` |
  | -цять (UA suffix) | -цать (RU) | `ц[ая]?т[ьъ]?(?:и|ю|ма)?` (the `(?:и|ю|ма)?` tail covers declensions: -ті/-ти gen/dat/loc, -тю/-тью instr, -тьма instr-pl) |
  | -надцять  | -надцать | `н?\\s*а[дт]\\s*ц[ая]?т[ьъ]?(?:и|ю|ма)?` (н? optional — UA has одинадцять, RU has одиннадцать) |

  Use these brackets ONLY where the mismatch is plausible (bad_token or samples show it). Do NOT add brackets everywhere.

## Context tags

The `context` field says WHAT the bot was asking. Different contexts should use different regex tightness:

  * `date`      — day/month names. Broad morphological coverage (many cases used).
  * `number`    — number words (cardinal + ordinal). ALWAYS apply space-injection + Cyrillic-alternation rules. Cover: space-split forms, Russian/Ukrainian spelling variants, double-consonant variants, AND case endings. Cardinal endings: nom/acc -ть, gen/dat/loc -ти (most common in phrases "до X", "о X годині"), instr -тьма/-тью. Ordinal endings: -ий/-ій/-ого/-ому/-им. Use `(?:и|ю|ма)?` after the -ть suffix to cover the main cardinal cases.
  * `plate`     — vehicle plate numbers. Tighten aggressively — false match is a real risk.
  * `city`, `address` — place names. Cover masc/fem/neuter endings.
  * `tire_size` — brand names, sizes. Narrow — brand hallucinations are rare and specific.
  * `time`      — hours words. Cover full paradigm + space injection (Ukrainian/Russian endings differ).
  * `any`       — no context. Be conservative — narrow pattern preferred.

## Output — JSON only, no prose outside

{
  "pattern":         "<the regex, no leading/trailing slashes>",
  "matched_forms":   ["<list of concrete strings the pattern will match — help the reviewer sanity-check>"],
  "reasoning":       "<one short sentence explaining the choice>"
}
"""


_MAX_SAMPLES_FOR_PROMPT = 5
_FALLBACK_MAX_MATCHED = 12


def _normalize_tokens(
    bad_token: str | None, bad_tokens: list[str] | None
) -> list[str]:
    """Merge the two input forms into a de-duped ordered list of non-empty tokens."""
    result: list[str] = []
    seen: set[str] = set()
    for tok in (bad_tokens or []):
        s = (tok or "").strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    if bad_token:
        s = bad_token.strip()
        if s and s not in seen:
            result.append(s)
            seen.add(s)
    return result


def _token_fragment_pattern(token: str) -> str:
    """Build the inner pattern for a single token (no outer \\b, no group).

    Space-injected tokens (containing whitespace) become fragments joined
    by ``\\s*`` — matches both the STT-split form and the fused form.
    """
    if " " in token:
        fragments = [re.escape(f) for f in token.strip().split()]
        return r"\s*".join(fragments)
    return re.escape(token)


def _fix_space_injection(pattern: str, bad_tokens: list[str]) -> str:
    """Replace literal spaces in pattern with \\s* when any token was space-fragmented.

    LLMs handle spaces in two ways:
      - Plain space:   \\bодин на дцать\\b   (GPT-style)
      - Escaped space: \\bодин\\ над\\ цать\\b  (Gemini-style — \\ followed by space)

    Both must become \\s* so all spacing variants match. The escaped-space
    case must be handled first: replacing \" \" first would turn \"\\ \" into
    \"\\\\s*\" (double-backslash) which breaks the regex.
    """
    if not any(" " in t for t in bad_tokens):
        return pattern
    has_escaped = "\\ " in pattern   # backslash + space (Gemini output)
    has_plain = " " in pattern        # plain space (GPT output)
    if not has_escaped and not has_plain:
        return pattern
    # Order matters: escaped-space first, then any remaining plain spaces.
    fixed = pattern.replace("\\ ", r"\s*").replace(" ", r"\s*")
    try:
        re.compile(fixed, re.IGNORECASE)
        if fixed != pattern:
            logger.info("regex_generator: space→\\s* fix applied: %r → %r", pattern, fixed)
        return fixed
    except re.error:
        return pattern  # leave untouched if the substitution somehow breaks it


def _fallback(
    bad_token: str | list[str],
    replacement: str,
    bad_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Safe default when the LLM cannot help.

    Accepts either a single ``bad_token`` (str), a list via the ``bad_tokens``
    kwarg, or a list directly as the first positional arg. Multiple tokens
    are combined into one alternation: ``\\b(?:tok1|tok2)\\b``.
    """
    # Normalise the positional arg — historical callers pass a string.
    if isinstance(bad_token, list):
        tokens = _normalize_tokens(None, bad_token)
        primary_str = tokens[0] if tokens else ""
    else:
        tokens = _normalize_tokens(bad_token, bad_tokens)
        primary_str = bad_token or (tokens[0] if tokens else "")

    if not tokens:
        # Degenerate case — return an impossible-to-match pattern rather than crash.
        return {
            "pattern": r"\b\Z\A\b",
            "matched_forms": [],
            "reasoning": "No heard-tokens provided.",
            "fallback": True,
        }

    if len(tokens) == 1:
        pattern = r"\b" + _token_fragment_pattern(tokens[0]) + r"\b"
    else:
        # Alternation: \b(?:tok1|tok2|tok3)\b — word boundaries at outer edges only.
        branches = "|".join(_token_fragment_pattern(t) for t in tokens)
        pattern = r"\b(?:" + branches + r")\b"

    reasoning_suffix = (
        " (single variant only)" if len(tokens) == 1
        else f" ({len(tokens)} variants combined via alternation)"
    )
    return {
        "pattern": pattern,
        "matched_forms": tokens,
        "reasoning": (
            "AI generation unavailable — using literal word-boundary match"
            + reasoning_suffix
            + "."
        ),
        "fallback": True,
        "primary_token": primary_str,
    }


def _validate_output(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Type-check + regex-compile. Returns cleaned dict or None on failure."""
    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        logger.warning("regex_generator: LLM returned no pattern")
        return None
    if len(pattern) > 500:
        logger.warning("regex_generator: LLM pattern too long (%d chars)", len(pattern))
        return None

    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        logger.warning("regex_generator: LLM produced invalid regex %r: %s", pattern, exc)
        return None

    matched = raw.get("matched_forms")
    if not isinstance(matched, list):
        matched = []
    matched = [str(x) for x in matched[:_FALLBACK_MAX_MATCHED] if x]

    reasoning = raw.get("reasoning")
    reasoning = str(reasoning) if reasoning else ""

    return {
        "pattern": pattern,
        "matched_forms": matched,
        "reasoning": reasoning,
        "fallback": False,
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """LLMs often wrap JSON in ```json ... ``` or add trailing prose. Be liberal."""
    if not text:
        return None

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        # Match the first {...} block.
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else text

    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.warning(
            "regex_generator: LLM output not valid JSON: %s | raw=%r",
            exc,
            text[:300],
        )
        return None
    return result if isinstance(result, dict) else None


def _build_user_message(
    bad_tokens: list[str],
    replacement: str,
    context: str,
    samples: list[str],
) -> str:
    """Compose the concrete task the LLM sees.

    ``bad_tokens`` — one or more heard variants that all mean the same thing.
    When >1 the LLM must produce ONE regex covering all of them (alternation
    plus shared morphology) rather than the first token only.
    """
    sample_lines = "\n".join(f"  - {s!r}" for s in samples[:_MAX_SAMPLES_FOR_PROMPT])
    if not sample_lines:
        sample_lines = "  (none provided)"

    # Explicit hint when STT has clearly split a single word into fragments.
    space_hint_parts = []
    for tok in bad_tokens:
        if " " in tok.strip():
            fragments = tok.strip().split()
            space_hint_parts.append(
                f"{tok!r} has {len(fragments)} fragments "
                f"({', '.join(repr(f) for f in fragments)})"
            )
    space_hint = ""
    if space_hint_parts:
        space_hint = (
            f"\n[SPACE-INJECTION DETECTED in: {'; '.join(space_hint_parts)}. "
            f"STT split a single word. Apply \\s* between fragments and \\b "
            f"only at the outer boundaries. Also apply Cyrillic alternation "
            f"at fragment joints.]"
        )

    if len(bad_tokens) > 1:
        heard_block = (
            "Bad tokens (multiple orthographic variants STT produced for the SAME meaning):\n"
            + "\n".join(f"  - {t!r}" for t in bad_tokens)
            + "\n"
            + "→ Produce ONE regex covering ALL variants. Use non-capturing alternation "
            + "`(?:variant1|variant2|...)` inside a single `\\b...\\b` pair. Extend each "
            + "variant with the appropriate morphological tail per rule #2. Do NOT create "
            + "separate regexes.\n"
        )
    else:
        heard_block = f"Bad token (what STT heard): {bad_tokens[0]!r}\n"

    return (
        f"{heard_block}"
        f"Replacement (what the caller meant): {replacement!r}\n"
        f"Bot context (what was being asked): {context}\n"
        f"Real transcripts where this token appeared:\n{sample_lines}\n"
        f"{space_hint}"
        f"\nProduce the regex per rules. JSON only."
    )


async def generate_regex(
    router: LLMRouter,
    *,
    bad_token: str | None = None,
    replacement: str,
    context: str,
    samples: list[str] | None = None,
    bad_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to build a regex for a suggestion.

    Accepts a single ``bad_token`` (back-compat) or a list ``bad_tokens``
    of orthographic variants that all mean the same thing. When >1 variant
    is supplied, the LLM is asked to combine them into ONE alternation
    regex (still with shared morphology). Returns a dict with keys:
    pattern, matched_forms, reasoning, fallback.
    Never raises — LLM failures degrade to _fallback() so the caller can
    still show *something* editable to the manager.
    """
    from src.llm.models import LLMTask

    tokens = _normalize_tokens(bad_token, bad_tokens)
    if not tokens:
        return _fallback("", replacement)

    samples = samples or []
    user_msg = _build_user_message(tokens, replacement, context, samples)

    try:
        response = await router.complete(
            task=LLMTask.REGEX_GENERATOR,
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM_PROMPT,
            max_tokens=1000,
        )
    except Exception:
        logger.exception("regex_generator: LLM call failed")
        return _fallback(tokens, replacement)

    raw = _extract_json(response.text)
    if not raw:
        return _fallback(tokens, replacement)

    cleaned = _validate_output(raw)
    if not cleaned:
        return _fallback(tokens, replacement)

    # Post-process: if LLM ignored the space-injection instruction and returned a
    # literal pattern with spaces, automatically replace them with \s*.
    cleaned["pattern"] = _fix_space_injection(cleaned["pattern"], tokens)

    # Sanity check: pattern must match at least one input token, either:
    #   (a) verbatim, OR
    #   (b) with internal spaces collapsed (space-injection case).
    # This guards against the LLM going completely off-topic. With multiple
    # heard variants we accept the LLM's regex as long as SOME variant matches
    # — a manual review still gates the create step.
    try:
        pattern_re = re.compile(cleaned["pattern"], re.IGNORECASE)
    except re.error:
        return _fallback(tokens, replacement)

    matched_any = False
    for tok in tokens:
        fused = re.sub(r"\s+", "", tok)
        if pattern_re.search(tok):
            matched_any = True
            break
        if fused != tok and pattern_re.search(fused):
            matched_any = True
            break

    if not matched_any:
        logger.warning(
            "regex_generator: LLM pattern %r does not match any of %r — falling back",
            cleaned["pattern"],
            tokens,
        )
        return _fallback(tokens, replacement)

    logger.info(
        "regex_generator: produced pattern %r (tokens=%d, forms=%d, provider=%s)",
        cleaned["pattern"],
        len(tokens),
        len(cleaned["matched_forms"]),
        response.provider,
    )
    return cleaned
