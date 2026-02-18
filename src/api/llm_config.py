"""Admin API for LLM multi-provider routing configuration.

Manage providers, task routes, health checks, and test prompts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis

from src.api.auth import require_role
from src.config import get_settings
from src.llm.models import DEFAULT_ROUTING_CONFIG
from src.llm.router import REDIS_CONFIG_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/llm", tags=["llm-routing"])

_redis: Redis | None = None

# Module-level dependency to satisfy B008 lint rule
_admin_dep = Depends(require_role("admin"))


async def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis


class ProviderUpdate(BaseModel):
    enabled: bool | None = None
    model: str | None = None


class TaskRouteUpdate(BaseModel):
    primary: str | None = None
    fallbacks: list[str] | None = None


class ConfigPatch(BaseModel):
    providers: dict[str, ProviderUpdate] | None = None
    tasks: dict[str, TaskRouteUpdate] | None = None


@router.get("/config")
async def get_llm_config(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get current LLM routing configuration (Redis + env fallback).

    API keys are masked — only env var names are exposed.
    """
    redis = await _get_redis()

    config_json = await redis.get(REDIS_CONFIG_KEY)
    redis_config = json.loads(config_json) if config_json else {}

    # Merge Redis over defaults
    config = _merge_config(redis_config)

    # Mask API keys — show env var name + whether key is set
    for _key, provider_cfg in config.get("providers", {}).items():
        env_name = provider_cfg.get("api_key_env", "")
        has_key = bool(os.environ.get(env_name, ""))
        provider_cfg["api_key_set"] = has_key
        # Never expose actual keys

    return {"config": config}


@router.patch("/config")
async def update_llm_config(
    request: ConfigPatch, _: dict[str, Any] = _admin_dep
) -> dict[str, Any]:
    """Update LLM routing config in Redis (merge patch)."""
    redis = await _get_redis()

    config_json = await redis.get(REDIS_CONFIG_KEY)
    config = json.loads(config_json) if config_json else {}

    if request.providers:
        if "providers" not in config:
            config["providers"] = {}
        for key, update in request.providers.items():
            if key not in config["providers"]:
                config["providers"][key] = {}
            if update.enabled is not None:
                config["providers"][key]["enabled"] = update.enabled
            if update.model is not None:
                config["providers"][key]["model"] = update.model

    if request.tasks:
        if "tasks" not in config:
            config["tasks"] = {}
        for task_name, update in request.tasks.items():
            if task_name not in config["tasks"]:
                config["tasks"][task_name] = {}
            if update.primary is not None:
                config["tasks"][task_name]["primary"] = update.primary
            if update.fallbacks is not None:
                config["tasks"][task_name]["fallbacks"] = update.fallbacks

    await redis.set(REDIS_CONFIG_KEY, json.dumps(config))
    logger.info("LLM routing config updated: %s", config)

    return {"message": "Config updated", "config": _merge_config(config)}


@router.get("/providers")
async def get_providers_health(_: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Get all providers with health status."""
    from src.main import _llm_router

    config = _merge_config({})

    providers_list: list[dict[str, Any]] = []
    for key, provider_cfg in config.get("providers", {}).items():
        env_name = provider_cfg.get("api_key_env", "")
        has_key = bool(os.environ.get(env_name, ""))

        info: dict[str, Any] = {
            "key": key,
            "type": provider_cfg.get("type", ""),
            "model": provider_cfg.get("model", ""),
            "enabled": provider_cfg.get("enabled", False),
            "api_key_set": has_key,
            "healthy": None,
        }

        # Check health if router is available and provider is active
        if _llm_router is not None and key in _llm_router.providers:
            try:
                info["healthy"] = await _llm_router.providers[key].health_check()
            except Exception:
                info["healthy"] = False

        providers_list.append(info)

    return {"providers": providers_list}


@router.post("/providers/{key}/test")
async def test_provider(key: str, _: dict[str, Any] = _admin_dep) -> dict[str, Any]:
    """Send a test prompt to a specific provider and return latency."""
    from src.main import _llm_router

    if _llm_router is None:
        raise HTTPException(status_code=400, detail="LLM routing is not enabled")

    if key not in _llm_router.providers:
        raise HTTPException(status_code=404, detail=f"Provider '{key}' not found or not enabled")

    provider = _llm_router.providers[key]

    start = time.monotonic()
    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": "Say 'hello' in one word."}],
            max_tokens=10,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "key": key,
            "success": True,
            "latency_ms": latency_ms,
            "response_text": response.text[:100],
            "model": response.model,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "key": key,
            "success": False,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _merge_config(redis_config: dict[str, Any]) -> dict[str, Any]:
    """Merge Redis config over defaults."""
    import copy

    config = copy.deepcopy(DEFAULT_ROUTING_CONFIG)
    if "providers" in redis_config:
        for key, val in redis_config["providers"].items():
            if key in config["providers"]:
                config["providers"][key].update(val)
            else:
                config["providers"][key] = val
    if "tasks" in redis_config:
        config["tasks"].update(redis_config["tasks"])
    return config
