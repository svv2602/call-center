"""Prompt management API endpoints.

CRUD for prompt versions and A/B test management.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID  # noqa: TC003 - FastAPI needs UUID at runtime for path params

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.ab_testing import ABTestManager
from src.agent.prompt_manager import PromptManager
from src.api.auth import require_role
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prompts", tags=["prompts"])

_engine: AsyncEngine | None = None

# Module-level dependencies to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))
_analyst_dep = Depends(require_role("admin", "analyst"))


async def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database.url)
    return _engine


# --- Request models ---


class CreatePromptRequest(BaseModel):
    name: str
    system_prompt: str
    tools_config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class CreateABTestRequest(BaseModel):
    test_name: str
    variant_a_id: UUID
    variant_b_id: UUID
    traffic_split: float = 0.5


# --- Prompt CRUD ---


@router.get("")
async def list_prompts(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """List all prompt versions."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    versions = await manager.list_versions()
    return {"versions": versions}


@router.post("")
async def create_prompt(
    request: CreatePromptRequest, _: dict[str, Any] = _admin_dep
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


@router.get("/{version_id}")
async def get_prompt(version_id: UUID, _: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """Get a specific prompt version."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    version = await manager.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return {"version": version}


@router.patch("/{version_id}/activate")
async def activate_prompt(version_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Activate a prompt version (deactivates all others)."""
    engine = await _get_engine()
    manager = PromptManager(engine)
    try:
        version = await manager.activate_version(version_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"version": version, "message": "Prompt version activated"}


# --- A/B Tests ---


@router.get("/ab-tests")
async def list_ab_tests(_: dict[str, Any] = _analyst_dep) -> dict[str, Any]:
    """List all A/B tests."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    tests = await ab_manager.list_tests()
    return {"tests": tests}


@router.post("/ab-tests")
async def create_ab_test(
    request: CreateABTestRequest, _: dict[str, Any] = _admin_dep
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
async def stop_ab_test(test_id: UUID, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Stop an A/B test and calculate final results."""
    engine = await _get_engine()
    ab_manager = ABTestManager(engine)
    try:
        test = await ab_manager.stop_test(test_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"test": test}
