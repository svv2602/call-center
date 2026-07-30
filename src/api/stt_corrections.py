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
    body: CorrectionRuleIn, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Create a new rule. Server generates the ``id``."""
    redis = await _get_redis()
    try:
        rule = await add_correction(redis, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("stt_corrections: created rule id=%s pattern=%s", rule["id"], rule["pattern"])
    return {"rule": rule}


@router.post("/corrections/bulk")
async def bulk_create_corrections(
    body: BulkRequest, _: dict[str, Any] = _perm_w
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

        rule["id"] = uuid.uuid4().hex[:12]
        rule.setdefault("enabled", True)
        rule.setdefault("flags", "i")
        rule.setdefault("context_hint", "")
        rule.setdefault("note", "")
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
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Merge non-None fields from ``body`` into the rule."""
    redis = await _get_redis()
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="empty patch")
    try:
        updated = await update_correction(redis, rule_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="rule not found")
    logger.info("stt_corrections: updated rule id=%s", rule_id)
    return {"rule": updated}


@router.delete("/corrections/{rule_id}")
async def remove_correction(
    rule_id: str, _: dict[str, Any] = _perm_w
) -> dict[str, str]:
    """Delete a rule by id."""
    redis = await _get_redis()
    ok = await delete_correction(redis, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    logger.info("stt_corrections: deleted rule id=%s", rule_id)
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
