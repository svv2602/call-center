"""Post-STT text corrections — deterministic regex substitutions applied
before the transcript reaches the LLM.

Motivation: Google STT reliably mishears certain telephone-audio words
into consistent wrong forms ("вівторок"→"вифт", "липня"→"лет",
"Дослідне"→"до сливного"). SpeechAdaptation phrase-hint boost only
helps when the model already has the right word in its top-K candidates
— for these hard cases it does not. Prompt-level fuzzy interpretation
catches some but not all.

Each rule is a regex → replacement pair, optionally scoped by
``context_hint`` (e.g. only apply "N лет → N липня" when the bot's last
utterance was asking for a date). Rules live in Redis so a content
manager can add/remove them without a deploy.

Storage: Redis key ``stt:corrections`` → JSON list of rule objects.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

REDIS_KEY = "stt:corrections"
_CACHE_TTL = 60.0  # seconds

# Valid context_hint values. ``None`` / empty = applies to any turn.
VALID_CONTEXTS: tuple[str, ...] = (
    "date",
    "time",
    "city",
    "plate",
    "station",
    "phone",
    "any",
)

# ─── In-process cache ─────────────────────────────────────
# (rules, compiled patterns aligned by index) — invalidated on write.
_cache_rules: list[dict[str, Any]] = []
_cache_patterns: list[re.Pattern[str] | None] = []
_cache_ts: float = 0.0


def _compile_rule(rule: dict[str, Any]) -> re.Pattern[str] | None:
    """Compile the rule's regex; return None on failure (rule is skipped)."""
    pattern = rule.get("pattern") or ""
    if not pattern:
        return None
    flags = 0
    flag_str = str(rule.get("flags") or "")
    if "i" in flag_str.lower():
        flags |= re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        logger.warning(
            "stt_corrections: invalid regex in rule id=%s: %s (%s)",
            rule.get("id"),
            pattern,
            exc,
        )
        return None


def validate_rule(rule: dict[str, Any]) -> None:
    """Raise ValueError on malformed rules (before persisting)."""
    pattern = rule.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    if len(pattern) > 500:
        raise ValueError("pattern too long (max 500 chars)")
    replacement = rule.get("replacement", "")
    if not isinstance(replacement, str):
        raise ValueError("replacement must be a string")
    if len(replacement) > 500:
        raise ValueError("replacement too long (max 500 chars)")
    # Test-compile so a bad regex is rejected up front rather than silently
    # dropped at runtime.
    flags = 0
    if "i" in str(rule.get("flags") or "").lower():
        flags |= re.IGNORECASE
    try:
        re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"invalid regex: {exc}") from exc
    context = rule.get("context_hint") or ""
    if context and context not in VALID_CONTEXTS:
        raise ValueError(
            f"context_hint must be one of {VALID_CONTEXTS} or empty"
        )


# ─── Redis persistence ────────────────────────────────────


async def load_corrections(redis: Redis, *, force: bool = False) -> list[dict[str, Any]]:
    """Load rules from Redis (with in-process cache).

    Returns a fresh list copy — callers may mutate it safely.
    """
    global _cache_rules, _cache_patterns, _cache_ts

    now = time.monotonic()
    if not force and _cache_rules and (now - _cache_ts) < _CACHE_TTL:
        return list(_cache_rules)

    rules: list[dict[str, Any]] = []
    try:
        raw = await redis.get(REDIS_KEY)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            if isinstance(data, list):
                rules = [r for r in data if isinstance(r, dict)]
    except Exception:
        logger.debug("stt_corrections: failed to read from Redis", exc_info=True)

    _cache_rules = rules
    _cache_patterns = [_compile_rule(r) for r in rules]
    _cache_ts = now
    return list(rules)


async def save_corrections(redis: Redis, rules: list[dict[str, Any]]) -> None:
    """Replace the full rules list; invalidates the in-process cache."""
    for r in rules:
        validate_rule(r)
    payload = json.dumps(rules, ensure_ascii=False)
    await redis.set(REDIS_KEY, payload)
    invalidate_cache()


async def add_correction(redis: Redis, rule: dict[str, Any]) -> dict[str, Any]:
    """Append a new rule, assigning an id if missing. Returns the stored rule."""
    validate_rule(rule)
    rule = dict(rule)  # shallow copy
    if not rule.get("id"):
        rule["id"] = uuid.uuid4().hex[:12]
    rule.setdefault("enabled", True)
    rule.setdefault("flags", "i")
    rule.setdefault("context_hint", "")
    rule.setdefault("note", "")
    existing = await load_corrections(redis, force=True)
    existing.append(rule)
    await save_corrections(redis, existing)
    return rule


async def update_correction(
    redis: Redis, rule_id: str, patch: dict[str, Any]
) -> dict[str, Any] | None:
    """Merge ``patch`` into the rule with matching id. Returns updated rule or None."""
    existing = await load_corrections(redis, force=True)
    updated: dict[str, Any] | None = None
    for i, r in enumerate(existing):
        if r.get("id") == rule_id:
            merged = {**r, **patch, "id": rule_id}
            validate_rule(merged)
            existing[i] = merged
            updated = merged
            break
    if updated is None:
        return None
    await save_corrections(redis, existing)
    return updated


async def delete_correction(redis: Redis, rule_id: str) -> bool:
    """Remove rule by id. Returns True if deleted, False if not found."""
    existing = await load_corrections(redis, force=True)
    filtered = [r for r in existing if r.get("id") != rule_id]
    if len(filtered) == len(existing):
        return False
    await save_corrections(redis, filtered)
    return True


# ─── Pure application function ────────────────────────────


def apply_rules(
    text: str,
    rules: list[dict[str, Any]],
    context_hint: str | None = None,
) -> tuple[str, list[str]]:
    """Apply rules to text in list order. Returns (new_text, applied_rule_ids).

    Rules are applied when:
      - enabled = True
      - context_hint matches, OR rule.context_hint is empty/"any"
    """
    if not text or not rules:
        return text, []

    ctx = (context_hint or "").strip().lower()
    result = text
    applied: list[str] = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rule_ctx = str(rule.get("context_hint") or "").strip().lower()
        if rule_ctx and rule_ctx != "any" and rule_ctx != ctx:
            continue

        pattern_str = rule.get("pattern") or ""
        if not pattern_str:
            continue
        flags = 0
        if "i" in str(rule.get("flags") or "").lower():
            flags |= re.IGNORECASE
        try:
            new_result, count = re.subn(pattern_str, rule.get("replacement", ""), result, flags=flags)
        except re.error:
            continue
        if count > 0 and new_result != result:
            result = new_result
            applied.append(str(rule.get("id") or "?"))

    return result, applied


async def apply_corrections(
    redis: Redis, text: str, context_hint: str | None = None
) -> tuple[str, list[str]]:
    """High-level entry: load rules (cached), apply, return (new_text, applied)."""
    rules = await load_corrections(redis)
    return apply_rules(text, rules, context_hint)


def invalidate_cache() -> None:
    """Force next load_corrections() to re-read from Redis."""
    global _cache_rules, _cache_patterns, _cache_ts
    _cache_rules = []
    _cache_patterns = []
    _cache_ts = 0.0
