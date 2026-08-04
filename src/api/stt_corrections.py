"""Admin API for post-STT correction rules.

Rules live in Redis (single key ``stt:corrections``, JSON list). Each rule
is a regex→replacement pair with optional context filter and enable flag.
See ``src/stt/corrections.py`` for the runtime.

Endpoints:
  GET    /admin/stt/corrections           — list all rules
  POST   /admin/stt/corrections           — create rule
  PUT    /admin/stt/corrections/{id}      — update rule (merge patch)
  DELETE /admin/stt/corrections/{id}      — delete rule
  POST   /admin/stt/corrections/test      — try rules against sample text
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import require_permission
from src.stt.corrections import (
    VALID_CONTEXTS,
    add_correction,
    apply_corrections,
    delete_correction,
    load_corrections,
    save_corrections,
    update_correction,
    validate_rule,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/stt", tags=["stt-corrections"])

_perm_r = Depends(require_permission("stt_corrections:read"))
_perm_w = Depends(require_permission("stt_corrections:write"))


async def _get_redis() -> Redis:
    from src.core.redis_client import get_redis

    return await get_redis()


def _who(user: dict[str, Any] | None) -> str:
    """Extract the acting username from a JWT payload, safely.

    Used as the ``reviewer`` field on any endpoint that mutates state.
    Trusting the JWT is safe because require_permission has already
    validated the token; using it (not a client-supplied field) prevents
    spoofing the audit trail.
    """
    if not user:
        return ""
    return str(user.get("sub") or user.get("user_id") or "")


class CorrectionRuleIn(BaseModel):
    """Input schema for create/update. ``id`` is server-generated on create."""

    pattern: str = Field(..., min_length=1, max_length=500)
    replacement: str = Field(default="", max_length=500)
    context_hint: str = Field(default="", description="One of: " + ", ".join(VALID_CONTEXTS))
    flags: str = Field(default="i", description="Regex flags — 'i' = case-insensitive")
    enabled: bool = True
    note: str = Field(default="", max_length=500)


class BulkRequest(BaseModel):
    """Payload for /corrections/bulk — a batch insert.

    ``skip_duplicates=True`` (default) drops rows whose ``pattern`` already
    exists in the current rule set — useful for re-running an import without
    creating duplicate rules. When ``False``, duplicates are still inserted
    (regex application is idempotent, so it is safe if the replacements match,
    but harmless duplicate rows accumulate).
    """

    rules: list[CorrectionRuleIn]
    skip_duplicates: bool = True


class CorrectionRulePatch(BaseModel):
    """Partial update — omit fields to keep current values."""

    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    replacement: str | None = Field(default=None, max_length=500)
    context_hint: str | None = None
    flags: str | None = None
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class TestRequest(BaseModel):
    text: str = Field(..., max_length=2000)
    context_hint: str = Field(default="", description="Simulate bot context")


@router.get("/corrections")
async def list_corrections(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return all correction rules and metadata."""
    redis = await _get_redis()
    rules = await load_corrections(redis, force=True)
    enabled = sum(1 for r in rules if r.get("enabled", True))
    return {
        "rules": rules,
        "count": len(rules),
        "enabled_count": enabled,
        "valid_contexts": list(VALID_CONTEXTS),
    }


@router.post("/corrections")
async def create_correction(
    body: CorrectionRuleIn, user: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Create a new rule. Server generates the ``id``."""
    redis = await _get_redis()
    import time

    payload = body.model_dump()
    payload["reviewer"] = _who(user)
    payload["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        rule = await add_correction(redis, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "stt_corrections: created rule id=%s by=%s pattern=%s",
        rule["id"], payload["reviewer"], rule["pattern"],
    )
    return {"rule": rule}


@router.post("/corrections/bulk")
async def bulk_create_corrections(
    body: BulkRequest, user: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Batch-insert rules. Each row is validated independently — a single bad
    row does not abort the rest. Returns per-row status plus totals.

    Response shape::

        {
          "created": <int>,
          "skipped": <int>,
          "errors": <int>,
          "results": [
            {"index": 0, "status": "created", "id": "..."},
            {"index": 1, "status": "skipped", "reason": "duplicate pattern"},
            {"index": 2, "status": "error", "error": "invalid regex: ..."},
          ]
        }

    Single Redis write at the end (batched) — safe for imports of 100s of rows.
    """
    import uuid

    redis = await _get_redis()
    existing = await load_corrections(redis, force=True)
    existing_patterns = {r.get("pattern") for r in existing}

    results: list[dict[str, Any]] = []
    to_add: list[dict[str, Any]] = []

    for idx, item in enumerate(body.rules):
        rule = item.model_dump()
        try:
            validate_rule(rule)
        except ValueError as exc:
            results.append({"index": idx, "status": "error", "error": str(exc)})
            continue

        if body.skip_duplicates and rule["pattern"] in existing_patterns:
            results.append(
                {"index": idx, "status": "skipped", "reason": "duplicate pattern"}
            )
            continue

        import time as _time

        rule["id"] = uuid.uuid4().hex[:12]
        rule.setdefault("enabled", True)
        rule.setdefault("flags", "i")
        rule.setdefault("context_hint", "")
        rule.setdefault("note", "")
        rule["reviewer"] = _who(user)
        rule["created_at"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        to_add.append(rule)
        existing_patterns.add(rule["pattern"])
        results.append({"index": idx, "status": "created", "id": rule["id"]})

    if to_add:
        await save_corrections(redis, existing + to_add)

    created = sum(1 for r in results if r["status"] == "created")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")

    logger.info(
        "stt_corrections: bulk import created=%d skipped=%d errors=%d",
        created,
        skipped,
        errors,
    )
    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


@router.put("/corrections/{rule_id}")
async def edit_correction(
    rule_id: str,
    body: CorrectionRulePatch,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Merge non-None fields from ``body`` into the rule."""
    import time

    redis = await _get_redis()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="empty patch")
    patch["last_edited_by"] = _who(user)
    patch["last_edited_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        updated = await update_correction(redis, rule_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="rule not found")
    logger.info(
        "stt_corrections: updated rule id=%s by=%s", rule_id, patch["last_edited_by"]
    )
    return {"rule": updated}


@router.delete("/corrections/{rule_id}")
async def remove_correction(
    rule_id: str, user: dict[str, Any] = _perm_w
) -> dict[str, str]:
    """Delete a rule by id."""
    redis = await _get_redis()
    ok = await delete_correction(redis, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    logger.info(
        "stt_corrections: deleted rule id=%s by=%s", rule_id, _who(user)
    )
    return {"status": "deleted", "id": rule_id}


@router.post("/corrections/test")
async def test_correction(
    body: TestRequest, _: dict[str, Any] = _perm_r
) -> dict[str, Any]:
    """Try current rules against sample text; return which rules fired.

    Does NOT persist anything — safe to call from the UI to preview
    behavior before saving a new rule.
    """
    redis = await _get_redis()
    ctx = body.context_hint.strip() or None
    new_text, applied = await apply_corrections(redis, body.text, ctx)
    return {
        "input": body.text,
        "context_hint": ctx,
        "output": new_text,
        "changed": new_text != body.text,
        "applied_rule_ids": applied,
    }


# ═══════════════════════════════════════════════════════════
#  Auto-suggested corrections (from call history)
# ═══════════════════════════════════════════════════════════
#
# A weekly Celery task (`rescan_stt_suggestions`) mines call_turns for
# tokens the STT keeps producing that aren't in any vocabulary and were
# followed by the bot re-asking. Each cluster lands here as a candidate
# rule — content manager approves it into the live rule set with one
# click.


class SuggestionListParams(BaseModel):
    """Query filters for /suggestions listing."""

    status: str | None = Field(default="pending", description="pending/approved/rejected/promoted")
    context: str | None = Field(default=None, description="Filter by detected_context")
    min_count: int = Field(default=1, ge=1)
    limit: int = Field(default=100, ge=1, le=500)


class SuggestionApprove(BaseModel):
    """Approve payload — content-manager can override pattern/replacement.

    ``generated_by`` records provenance for future audit — 'ai' when the
    regex came from generate-regex, 'manual' when the manager typed or
    heavily edited it, NULL when they accepted the scanner default.
    """

    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    replacement: str | None = Field(default=None, max_length=500)
    context_hint: str | None = Field(default=None)
    note: str | None = Field(default=None, max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)
    generated_by: str | None = Field(default=None, description="ai / manual / null")


class GenerateRegexRequest(BaseModel):
    """Ask the LLM to build a regex for a suggestion.

    ``replacement`` is the human-readable "what the customer meant";
    ``context_hint`` narrows the morphology dictionary the LLM draws from.
    Both are optional overrides — defaults come from the stored suggestion.
    """

    replacement: str | None = Field(default=None, max_length=500)
    context_hint: str | None = Field(default=None)


class GenerateRegexDirectRequest(BaseModel):
    """Generate regex directly from a bad_token without a stored suggestion."""

    bad_token: str = Field(min_length=1, max_length=500)
    replacement: str = Field(default="", max_length=500)
    context_hint: str | None = Field(default=None)


class PreviewRequest(BaseModel):
    """Preview a candidate rule against recent call_turns."""

    pattern: str = Field(..., min_length=1, max_length=500)
    replacement: str = Field(default="", max_length=500)
    context_hint: str = Field(default="")
    days: int = Field(default=7, ge=1, le=30)


class SuggestionReject(BaseModel):
    """Reject payload with optional reason for future filtering."""

    reason: str | None = Field(default=None, max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)


class RescanRequest(BaseModel):
    """Manual rescan trigger — same params as the Celery task."""

    days: int = Field(default=30, ge=1, le=90)
    min_occurrences: int = Field(default=2, ge=1, le=50)


async def _get_engine() -> Any:
    """Fetch the shared async SQLAlchemy engine used across the API."""
    from src.api.database import get_engine

    return await get_engine()


_SQL_LIST_SUGGESTIONS = """
    SELECT
        id::text, bad_token, detected_context, occurrence_count,
        sample_transcripts, proposed_pattern, proposed_replacement,
        match_distance, status, created_rule_id, reviewer, reviewed_at,
        reject_reason, first_seen_at, last_seen_at
    FROM stt_correction_suggestions
    WHERE (CAST(:status AS text) = '' OR status = CAST(:status AS text))
      AND (CAST(:context AS text) = '' OR detected_context = CAST(:context AS text))
      AND occurrence_count >= :min_count
    ORDER BY occurrence_count DESC, last_seen_at DESC
    LIMIT :limit
"""


@router.get("/corrections/suggestions")
async def list_suggestions(
    status: str = "pending",
    context: str | None = None,
    min_count: int = 1,
    limit: int = 100,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List auto-generated correction suggestions with counts and samples."""
    from sqlalchemy import text

    engine = await _get_engine()
    # Empty string = "no filter" — see _SQL_LIST_SUGGESTIONS. asyncpg cannot
    # infer the type of a NULL bind param without help, so we use '' instead.
    status_arg = "" if status.lower() in ("all", "*", "") else status
    context_arg = context or ""

    async with engine.begin() as conn:
        result = await conn.execute(
            text(_SQL_LIST_SUGGESTIONS),
            {
                "status": status_arg,
                "context": context_arg,
                "min_count": max(min_count, 1),
                "limit": min(max(limit, 1), 500),
            },
        )
        rows = [dict(r._mapping) for r in result]

    for row in rows:
        for k in ("first_seen_at", "last_seen_at", "reviewed_at"):
            if row.get(k) is not None:
                row[k] = row[k].isoformat()
        if row.get("id") is not None:
            row["id"] = str(row["id"])

    return {"suggestions": rows, "count": len(rows)}


_SQL_GET_SUGGESTION = """
    SELECT id::text, bad_token, detected_context, proposed_pattern,
           proposed_replacement, status
    FROM stt_correction_suggestions
    WHERE id = :id
"""

_SQL_MARK_PROMOTED = """
    UPDATE stt_correction_suggestions
    SET status = 'promoted',
        created_rule_id = :rule_id,
        reviewer = :reviewer,
        reviewed_at = NOW(),
        generated_by = COALESCE(:generated_by, generated_by)
    WHERE id = :id
"""

_SQL_MARK_REJECTED = """
    UPDATE stt_correction_suggestions
    SET status = 'rejected',
        reject_reason = :reason,
        reviewer = :reviewer,
        reviewed_at = NOW()
    WHERE id = :id
"""


@router.post("/corrections/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: str,
    body: SuggestionApprove,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Promote a suggestion into a real correction rule.

    Manager may override pattern/replacement/context — otherwise the
    stored proposals are used. On success, returns the created rule and
    marks the suggestion as ``promoted``.
    """
    from sqlalchemy import text

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(_SQL_GET_SUGGESTION), {"id": suggestion_id}
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"already {row['status']}, cannot promote again"
        )

    pattern = body.pattern or row["proposed_pattern"]
    replacement = body.replacement if body.replacement is not None else (
        row["proposed_replacement"] or ""
    )
    context_hint = (
        body.context_hint
        if body.context_hint is not None
        else (row["detected_context"] if row["detected_context"] != "any" else "")
    )

    if not pattern:
        raise HTTPException(
            status_code=400,
            detail="pattern is empty — provide `pattern` in body or run rescan first",
        )

    import time as _time

    redis = await _get_redis()
    reviewer = _who(user)
    rule_payload = {
        "pattern": pattern,
        "replacement": replacement,
        "context_hint": context_hint,
        "enabled": True,
        "flags": "i",
        "note": body.note or f"auto-suggested from token '{row['bad_token']}'",
        "reviewer": reviewer,
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    try:
        rule = await add_correction(redis, rule_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with engine.begin() as conn:
        await conn.execute(
            text(_SQL_MARK_PROMOTED),
            {
                "id": suggestion_id,
                "rule_id": rule["id"],
                "reviewer": reviewer,
                "generated_by": body.generated_by,
            },
        )

    from src.monitoring.metrics import stt_correction_suggestions_promoted_total

    stt_correction_suggestions_promoted_total.labels(
        context=row["detected_context"] or "any"
    ).inc()

    logger.info(
        "stt_corrections: promoted suggestion %s → rule %s (token=%s)",
        suggestion_id,
        rule["id"],
        row["bad_token"],
    )
    return {"rule": rule, "suggestion_id": suggestion_id}


@router.post("/corrections/suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: str,
    body: SuggestionReject,
    user: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Mark a suggestion as rejected — it won't be re-surfaced on rescan."""
    from sqlalchemy import text

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(_SQL_MARK_REJECTED),
            {
                "id": suggestion_id,
                "reason": body.reason or "",
                "reviewer": _who(user),
            },
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="suggestion not found")

    logger.info("stt_corrections: rejected suggestion %s", suggestion_id)
    return {"status": "rejected", "id": suggestion_id}


@router.post("/corrections/suggestions/rescan")
async def rescan_suggestions(
    body: RescanRequest | None = None,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Trigger the Celery rescan asynchronously; returns the task id."""
    from src.tasks.stt_suggestions_tasks import rescan_stt_suggestions

    params = body or RescanRequest()
    async_result = rescan_stt_suggestions.delay(  # type: ignore[attr-defined]
        days=params.days,
        min_occurrences=params.min_occurrences,
        triggered_by="manual",
    )
    logger.info(
        "stt_corrections: rescan task queued id=%s days=%d min_occ=%d",
        async_result.id,
        params.days,
        params.min_occurrences,
    )
    return {
        "task_id": async_result.id,
        "days": params.days,
        "min_occurrences": params.min_occurrences,
    }


_SQL_GET_SUGGESTION_FULL = """
    SELECT id::text, bad_token, detected_context, proposed_pattern,
           proposed_replacement, sample_transcripts
    FROM stt_correction_suggestions
    WHERE id = :id
"""


@router.post("/corrections/suggestions/{suggestion_id}/generate-regex")
async def suggestion_generate_regex(
    suggestion_id: str,
    body: GenerateRegexRequest,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Have the LLM build a morphology-aware regex for this suggestion.

    Returns {pattern, matched_forms, reasoning, fallback}. Does not
    persist — the modal shows the result for the manager to review /
    edit / approve. `fallback: true` means the LLM was unavailable and
    the response degraded to `\\bTOKEN\\b`.
    """
    from sqlalchemy import text as sql_text

    from src.llm import get_router

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            sql_text(_SQL_GET_SUGGESTION_FULL), {"id": suggestion_id}
        )
        row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    router_obj = get_router()
    if router_obj is None:
        # Router not initialised (e.g. FF_LLM_ROUTING_ENABLED=false) —
        # fall back to the deterministic default rather than 500ing.
        import re as re_mod

        from src.stt.regex_generator import _fallback

        _ = re_mod  # keep import used
        return {
            **_fallback(row["bad_token"], body.replacement or row["proposed_replacement"] or ""),
            "router_available": False,
        }

    replacement = (body.replacement or row["proposed_replacement"] or "").strip()
    context = (body.context_hint or row["detected_context"] or "any").strip() or "any"
    samples_raw = row["sample_transcripts"] or []
    samples = [s.get("text", "") for s in samples_raw if isinstance(s, dict)]

    from src.stt.regex_generator import generate_regex

    result_dict = await generate_regex(
        router_obj,
        bad_token=row["bad_token"],
        replacement=replacement,
        context=context,
        samples=samples,
    )
    return {**result_dict, "router_available": True}


@router.post("/corrections/generate-regex")
async def generate_regex_direct(
    body: GenerateRegexDirectRequest,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Generate a regex directly from bad_token without a stored suggestion.

    Used by the manual "Add rule" flow where no suggestion_id exists.
    Calls the same LLM pipeline as suggestion_generate_regex but with
    empty samples (no transcript context).
    """
    from src.llm import get_router

    router_obj = get_router()
    if router_obj is None:
        from src.stt.regex_generator import _fallback

        return {**_fallback(body.bad_token, body.replacement), "router_available": False}

    context = (body.context_hint or "any").strip() or "any"

    from src.stt.regex_generator import generate_regex

    result_dict = await generate_regex(
        router_obj,
        bad_token=body.bad_token,
        replacement=body.replacement,
        context=context,
        samples=[],
    )
    return {**result_dict, "router_available": True}


@router.get("/corrections/check-similar")
async def check_similar_rules(
    token: str,
    context: str = "",
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Find already-existing rules whose pattern matches ``token``.

    Used by the approve modal to warn the manager before they create a
    potentially-duplicate rule. Returns up to 5 matches ordered by rule
    position (older rules first — usually more general / longer-standing).

    Context filtering: a rule whose context is 'any' or empty always
    matches; a rule whose context differs from the caller's is skipped.
    """
    import re as re_mod

    if not token:
        return {"matches": []}

    redis = await _get_redis()
    rules = await load_corrections(redis, force=True)

    caller_ctx = (context or "").strip().lower()
    matches: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rule_ctx = str(rule.get("context_hint") or "").strip().lower()
        if rule_ctx and rule_ctx != "any" and caller_ctx and rule_ctx != caller_ctx:
            continue
        pattern = rule.get("pattern") or ""
        if not pattern:
            continue
        try:
            if re_mod.search(pattern, token, re_mod.IGNORECASE):
                matches.append(
                    {
                        "id": rule.get("id"),
                        "pattern": pattern,
                        "replacement": rule.get("replacement", ""),
                        "context_hint": rule.get("context_hint") or "",
                        "note": rule.get("note", ""),
                    }
                )
                if len(matches) >= 5:
                    break
        except re_mod.error:
            continue

    return {"matches": matches, "token": token, "checked_context": caller_ctx}


_SQL_CALL_CONTEXT = """
    SELECT turn_number, speaker,
           content AS text,
           ROUND(stt_confidence::numeric, 2) AS confidence,
           language,
           created_at
    FROM call_turns
    WHERE call_id = :call_id
      AND turn_number BETWEEN :from_turn AND :to_turn
    ORDER BY turn_number
"""


@router.get("/corrections/call-context")
async def get_call_context(
    call_id: str,
    turn: int,
    window: int = 5,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Return N turns around the flagged one for a suggestion sample.

    Scoped to stt_corrections:read (not analytics:read) so that
    content_manager can inspect the surrounding context without needing
    full analytics access. Returns only the requested slice of one call.
    """
    from sqlalchemy import text as sql_text

    if window < 1 or window > 20:
        raise HTTPException(status_code=400, detail="window must be 1..20")

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            sql_text(_SQL_CALL_CONTEXT),
            {
                "call_id": call_id,
                "from_turn": max(0, turn - window),
                "to_turn": turn + window,
            },
        )
        rows = [dict(r._mapping) for r in result]

    for row in rows:
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()

    return {
        "call_id": call_id,
        "target_turn": turn,
        "window": window,
        "turns": rows,
    }


@router.post("/corrections/preview")
async def preview_correction_endpoint(
    body: PreviewRequest,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """Apply a candidate pattern to recent call_turns; return match diffs.

    Splits matches into `expected` (turn was followed by bot re-ask —
    correction is helping) and `unexpected` (turn was successful —
    correction may be a false positive). The UI shows the second bucket
    as a yellow warning.
    """
    from src.stt.correction_preview import preview_correction

    engine = await _get_engine()
    result = await preview_correction(
        engine,
        pattern=body.pattern,
        replacement=body.replacement,
        context_hint=body.context_hint,
        days=body.days,
    )
    return result
