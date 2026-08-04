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

1. **Always use `\\b` word boundaries** around matched forms. Never leave a raw substring that could match inside another word.
2. **Cover morphological forms of the source noun/adjective/verb** when it's a real Ukrainian/Russian word. For "приятный" also match "приятная", "приятного", "приятную", "приятные", "приятненький". For "виктора" also match "викторе", "викторов", "викторовна".
3. **Use `\\w+` sparingly** — only when the samples show variable endings AND you can't enumerate them cleanly. Prefer `(форма1|форма2|форма3)` explicit alternation.
4. **DO NOT over-generalise**. If samples all say "на X", make the regex require the "на " prefix. If samples show different prefixes, drop the prefix requirement.
5. **Case-insensitive flag `i` is applied by the runtime** — do NOT emit `(?i)` in the pattern, and don't add capital-letter alternatives.
6. **Replacement is literal** — no backreferences unless you also captured a group you want to preserve (rare — usually the prefix/suffix is discarded).
7. **Never invent samples**. Only use what's provided.

## Context tags

The `context` field says WHAT the bot was asking. Different contexts should use different regex tightness:

  * `date`      — day/month names. Broad morphological coverage (many cases used).
  * `plate`     — vehicle plate numbers. Tighten aggressively — false match is a real risk.
  * `city`, `address` — place names. Cover masc/fem/neuter endings.
  * `tire_size` — brand names, sizes. Narrow — brand hallucinations are rare and specific.
  * `time`      — hours words. Cover full paradigm.
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


def _fallback(bad_token: str, replacement: str) -> dict[str, Any]:
    """Safe default when the LLM cannot help."""
    escaped = re.escape(bad_token)
    return {
        "pattern": rf"\b{escaped}\b",
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
        logger.warning("regex_generator: LLM output not valid JSON: %s", exc)
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

    return (
        f"Bad token (what STT heard): {bad_token!r}\n"
        f"Replacement (what the caller meant): {replacement!r}\n"
        f"Bot context (what was being asked): {context}\n"
        f"Real transcripts where this token appeared:\n{sample_lines}\n"
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
        # Reuse PROMPT_OPTIMIZER task — semantically closest (structured
        # configuration built from context), and it's already wired to a
        # cheap model in every deployed routing config.
        response = await router.complete(
            task=LLMTask.PROMPT_OPTIMIZER,
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM_PROMPT,
            max_tokens=400,
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

    # Sanity check: pattern must actually match the token we started from,
    # otherwise the LLM went off-topic and produced something unrelated.
    try:
        if not re.search(cleaned["pattern"], bad_token, re.IGNORECASE):
            logger.warning(
                "regex_generator: LLM pattern %r does not match bad_token %r — falling back",
                cleaned["pattern"],
                bad_token,
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
