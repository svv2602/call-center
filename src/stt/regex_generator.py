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


def _fix_space_injection(pattern: str, bad_token: str) -> str:
    """Replace literal spaces in pattern with \\s* when bad_token was space-fragmented.

    LLMs handle spaces in two ways:
      - Plain space:   \\bодин на дцать\\b   (GPT-style)
      - Escaped space: \\bодин\\ над\\ цать\\b  (Gemini-style — \\ followed by space)

    Both must become \\s* so all spacing variants match. The escaped-space
    case must be handled first: replacing \" \" first would turn \"\\ \" into
    \"\\\\s*\" (double-backslash) which breaks the regex.
    """
    if " " not in bad_token:
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


def _fallback(bad_token: str, replacement: str) -> dict[str, Any]:
    """Safe default when the LLM cannot help.

    When bad_token contains spaces the token was STT-fragmented; use \\s*
    between fragments so the pattern matches both spaced and fused forms.
    Python 3.12 re.escape() escapes spaces as "\\ " (for verbose-mode
    safety), so we bypass re.escape for space-containing tokens.
    """
    if " " in bad_token:
        fragments = [re.escape(f) for f in bad_token.strip().split()]
        pattern = r"\b" + r"\s*".join(fragments) + r"\b"
    else:
        pattern = rf"\b{re.escape(bad_token)}\b"
    return {
        "pattern": pattern,
        "matched_forms": [bad_token],
        "reasoning": (
            "AI generation unavailable — using literal word-boundary match "
            "for the observed token only."
        ),
        "fallback": True,
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
    bad_token: str,
    replacement: str,
    context: str,
    samples: list[str],
) -> str:
    """Compose the concrete task the LLM sees."""
    sample_lines = "\n".join(f"  - {s!r}" for s in samples[:_MAX_SAMPLES_FOR_PROMPT])
    if not sample_lines:
        sample_lines = "  (none provided)"

    # Explicit hint when STT has clearly split a single word into fragments.
    space_hint = ""
    if " " in bad_token.strip():
        fragments = bad_token.strip().split()
        space_hint = (
            f"\n[SPACE-INJECTION DETECTED: bad_token has {len(fragments)} fragments "
            f"({', '.join(repr(f) for f in fragments)}). "
            f"This is almost certainly one word that STT split. "
            f"Apply \\s* between fragments and \\b only at the outer boundaries. "
            f"Also apply Cyrillic alternation at fragment joints.]"
        )

    return (
        f"Bad token (what STT heard): {bad_token!r}\n"
        f"Replacement (what the caller meant): {replacement!r}\n"
        f"Bot context (what was being asked): {context}\n"
        f"Real transcripts where this token appeared:\n{sample_lines}\n"
        f"{space_hint}"
        f"\nProduce the regex per rules. JSON only."
    )


async def generate_regex(
    router: LLMRouter,
    *,
    bad_token: str,
    replacement: str,
    context: str,
    samples: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the LLM to build a regex for a suggestion.

    Returns a dict with keys: pattern, matched_forms, reasoning, fallback.
    Never raises — LLM failures degrade to _fallback() so the caller can
    still show *something* editable to the manager.
    """
    from src.llm.models import LLMTask

    samples = samples or []
    user_msg = _build_user_message(bad_token, replacement, context, samples)

    try:
        response = await router.complete(
            task=LLMTask.REGEX_GENERATOR,
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM_PROMPT,
            max_tokens=1000,
        )
    except Exception:
        logger.exception("regex_generator: LLM call failed")
        return _fallback(bad_token, replacement)

    raw = _extract_json(response.text)
    if not raw:
        return _fallback(bad_token, replacement)

    cleaned = _validate_output(raw)
    if not cleaned:
        return _fallback(bad_token, replacement)

    # Post-process: if LLM ignored the space-injection instruction and returned a
    # literal pattern with spaces, automatically replace them with \s*.
    cleaned["pattern"] = _fix_space_injection(cleaned["pattern"], bad_token)

    # Sanity check: pattern must match either:
    #   (a) the bad_token verbatim, OR
    #   (b) the bad_token with all internal spaces collapsed (space-injection case).
    # This guards against the LLM going completely off-topic.
    fused = re.sub(r"\s+", "", bad_token)  # "один над цать" → "однадцать"
    try:
        matches_verbatim = re.search(cleaned["pattern"], bad_token, re.IGNORECASE)
        matches_fused = fused != bad_token and re.search(
            cleaned["pattern"], fused, re.IGNORECASE
        )
        if not matches_verbatim and not matches_fused:
            logger.warning(
                "regex_generator: LLM pattern %r does not match bad_token %r or fused %r — falling back",
                cleaned["pattern"],
                bad_token,
                fused,
            )
            return _fallback(bad_token, replacement)
    except re.error:
        return _fallback(bad_token, replacement)

    logger.info(
        "regex_generator: produced pattern %r (forms=%d, provider=%s)",
        cleaned["pattern"],
        len(cleaned["matched_forms"]),
        response.provider,
    )
    return cleaned
