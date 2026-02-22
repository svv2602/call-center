"""Prompt management API endpoints.

CRUD for prompt versions and A/B test management.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.ab_testing import QUALITY_CRITERIA, ABTestManager
from src.agent.prompt_manager import PromptManager
from src.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from src.api.auth import require_permission
from src.api.export import _csv_streaming_response
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prompts", tags=["prompts"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_perm_r = Depends(require_permission("prompts:read"))
_perm_w = Depends(require_permission("prompts:write"))
_perm_d = Depends(require_permission("prompts:delete"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url, pool_pre_ping=True)
    return _engine


# --- Request models ---


class CreatePromptRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(min_length=1)
    tools_config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class CreateABTestRequest(BaseModel):
    test_name: str = Field(min_length=1, max_length=200)
    variant_a_id: UUID
    variant_b_id: UUID
    traffic_split: float = Field(default=0.5, ge=0.0, le=1.0)


# --- Prompt CRUD ---


@router.get("")
async def list_prompts(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """List all prompt versions."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    versions = await manager.list_versions()
    return {"versions": versions}


@router.post("")
async def create_prompt(
    request: CreatePromptRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Create a new prompt version."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    version = await manager.create_version(
        name=request.name,
        system_prompt=request.system_prompt,
        tools_config=request.tools_config,
        metadata=request.metadata,
    )
    return {"version": version}


@router.get("/default")
async def get_default_prompt(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Return the hardcoded factory-default system prompt."""
    return {"name": PROMPT_VERSION, "system_prompt": SYSTEM_PROMPT}


# --- A/B Tests (must be before /{version_id} to avoid matching "ab-tests" as UUID) ---


@router.get("/ab-tests")
async def list_ab_tests(_: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """List all A/B tests."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    tests = await ab_manager.list_tests()
    return {"tests": tests}


@router.post("/ab-tests")
async def create_ab_test(
    request: CreateABTestRequest, _: dict[str, Any] = _perm_w
) -> dict[str, Any]:
    """Create a new A/B test (stops any existing active test)."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    test = await ab_manager.create_test(
        test_name=request.test_name,
        variant_a_id=request.variant_a_id,
        variant_b_id=request.variant_b_id,
        traffic_split=request.traffic_split,
    )
    return {"test": test}


@router.patch("/ab-tests/{test_id}/stop")
async def stop_ab_test(test_id: UUID, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Stop an A/B test and calculate final results."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    try:
        test = await ab_manager.stop_test(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"test": test}


@router.delete("/ab-tests/{test_id}")
async def delete_ab_test(test_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Delete an A/B test (must be stopped first)."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    try:
        await ab_manager.delete_test(test_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"deleted": True}


@router.get("/ab-tests/{test_id}/report")
async def get_ab_test_report(test_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get a detailed analytics report for an A/B test."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    try:
        report = await ab_manager.get_report(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return report


@router.get("/ab-tests/{test_id}/report/csv")
async def export_ab_test_report_csv(test_id: UUID, _: dict[str, Any] = _perm_r) -> Any:
    """Export A/B test report as CSV (daily rows + TOTAL)."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    try:
        report = await ab_manager.get_report(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    columns = ["date", "calls_a", "calls_b", "quality_a", "quality_b"]
    # Add per-criterion columns
    for c in QUALITY_CRITERIA:
        columns.append(f"criterion_{c}_a")
        columns.append(f"criterion_{c}_b")

    # Build criterion lookup
    criterion_map = {cr["criterion"]: cr for cr in report.get("per_criterion", [])}

    rows: list[dict[str, Any]] = []
    for d in report.get("daily", []):
        row: dict[str, Any] = {
            "date": d["date"],
            "calls_a": d["calls_a"],
            "calls_b": d["calls_b"],
            "quality_a": d["quality_a"] if d["quality_a"] is not None else "",
            "quality_b": d["quality_b"] if d["quality_b"] is not None else "",
        }
        for c in QUALITY_CRITERIA:
            row[f"criterion_{c}_a"] = ""
            row[f"criterion_{c}_b"] = ""
        rows.append(row)

    # TOTAL row
    summary = report.get("summary", {})
    total_row: dict[str, Any] = {
        "date": "TOTAL",
        "calls_a": summary.get("calls_a", 0),
        "calls_b": summary.get("calls_b", 0),
        "quality_a": summary.get("quality_a", ""),
        "quality_b": summary.get("quality_b", ""),
    }
    for c in QUALITY_CRITERIA:
        cr = criterion_map.get(c, {})
        total_row[f"criterion_{c}_a"] = cr.get("avg_a", "")
        total_row[f"criterion_{c}_b"] = cr.get("avg_b", "")
    rows.append(total_row)

    test_name = report.get("test", {}).get("test_name", "ab_test")
    filename = f"ab_report_{test_name}.csv"
    return _csv_streaming_response(rows, columns, filename)


# --- Prompt Optimizer ---


@router.get("/optimizer/results")
async def list_optimization_results(
    limit: int = 10,
    _: dict[str, Any] = _perm_r,
) -> dict[str, Any]:
    """List recent prompt optimization analysis results."""
    from sqlalchemy import text as sa_text

    engine = await _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            sa_text("""
                SELECT id, days_analyzed, calls_analyzed, patterns,
                       overall_recommendation, status, error,
                       triggered_by, created_at
                FROM prompt_optimization_results
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        items = [dict(r._mapping) for r in result]
    return {"items": items}


@router.post("/optimizer/run")
async def run_prompt_optimization(
    days: int = 7,
    max_calls: int = 20,
    _: dict[str, Any] = _perm_w,
) -> dict[str, Any]:
    """Trigger a prompt optimization analysis (async Celery task)."""
    from src.tasks.prompt_optimizer import analyze_failed_calls

    task = analyze_failed_calls.delay(days=days, max_calls=max_calls, triggered_by="manual")
    return {"task_id": task.id, "message": "Prompt optimization analysis started"}


# --- Prompt by ID (after /ab-tests to avoid route conflict) ---


@router.get("/{version_id}")
async def get_prompt(version_id: UUID, _: dict[str, Any] = _perm_r) -> dict[str, Any]:
    """Get a specific prompt version."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    version = await manager.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return {"version": version}


@router.delete("/{version_id}")
async def delete_prompt(version_id: UUID, _: dict[str, Any] = _perm_d) -> dict[str, Any]:
    """Delete a prompt version (cannot delete active or A/B-tested)."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    try:
        await manager.delete_version(version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"deleted": True}


@router.patch("/{version_id}/activate")
async def activate_prompt(version_id: UUID, _: dict[str, Any] = _perm_w) -> dict[str, Any]:
    """Activate a prompt version (deactivates all others)."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    try:
        version = await manager.activate_version(version_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"version": version, "message": "Prompt version activated"}
