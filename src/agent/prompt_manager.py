"""Prompt version management.

Stores and retrieves prompt versions from PostgreSQL.
Falls back to hardcoded prompt if database is unavailable.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from src.agent.prompts import (
    ERROR_TEXT,
    FAREWELL_TEXT,
    GREETING_TEXT,
    ORDER_CANCELLED_TEXT,
    PROMPT_VERSION,
    PRONUNCIATION_RULES,
    SILENCE_PROMPT_TEXT,
    SYSTEM_PROMPT,
    TRANSFER_TEXT,
    WAIT_TEXT,
)

if TYPE_CHECKING:
    from uuid import UUID

    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# ── Dialogue / safety / promotions caches ──────────────────
_dialogue_cache: dict[str, list[dict[str, Any]]] = {}
_dialogue_cache_ts: float = 0.0

_safety_cache: list[dict[str, Any]] = []
_safety_cache_ts: float = 0.0

_promos_cache: dict[str, list[dict[str, str]]] = {}  # keyed by tenant_id
_promos_cache_ts: float = 0.0

DIALOGUE_CACHE_REDIS_KEY = "training:dialogue_cache_ts"
SAFETY_CACHE_REDIS_KEY = "training:safety_cache_ts"
PROMOS_CACHE_REDIS_KEY = "promotions:cache_ts"


class PromptManager:
    """Manages prompt versions stored in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_active_prompt(self) -> dict[str, Any]:
        """Get the currently active prompt version.

        Returns:
            Dict with id, name, system_prompt, tools_config, metadata.
            Falls back to hardcoded prompt if DB unavailable.
        """
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT id, name, system_prompt, tools_config, metadata
                        FROM prompt_versions
                        WHERE is_active = true
                        ORDER BY created_at DESC
                        LIMIT 1
                    """)
                )
                row = result.first()

            if row:
                return dict(row._mapping)

        except Exception:
            logger.warning("Failed to load prompt from DB, using hardcoded fallback")

        return {
            "id": None,
            "name": PROMPT_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "tools_config": None,
            "metadata": {},
        }

    async def create_version(
        self,
        name: str,
        system_prompt: str,
        tools_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new prompt version."""
        import json

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO prompt_versions (name, system_prompt, tools_config, metadata)
                    VALUES (:name, :system_prompt, :tools_config, :metadata)
                    RETURNING id, name, system_prompt, is_active, created_at
                """),
                {
                    "name": name,
                    "system_prompt": system_prompt,
                    "tools_config": json.dumps(tools_config) if tools_config else None,
                    "metadata": json.dumps(metadata or {}),
                },
            )
            row = result.first()
            if row is None:
                msg = "Expected row from INSERT RETURNING"
                raise RuntimeError(msg)
            return dict(row._mapping)

    async def activate_version(self, version_id: UUID) -> dict[str, Any]:
        """Activate a prompt version (deactivates all others)."""
        async with self._engine.begin() as conn:
            # Deactivate all
            await conn.execute(
                text("UPDATE prompt_versions SET is_active = false WHERE is_active = true")
            )
            # Activate target
            result = await conn.execute(
                text("""
                    UPDATE prompt_versions
                    SET is_active = true
                    WHERE id = :version_id
                    RETURNING id, name, is_active, created_at
                """),
                {"version_id": str(version_id)},
            )
            row = result.first()
            if not row:
                msg = f"Prompt version {version_id} not found"
                raise ValueError(msg)
            return dict(row._mapping)

    async def list_versions(self) -> list[dict[str, Any]]:
        """List all prompt versions."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, name, is_active, metadata, created_at
                    FROM prompt_versions
                    ORDER BY created_at DESC
                """)
            )
            return [dict(row._mapping) for row in result]

    async def get_active_templates(self) -> dict[str, str]:
        """Get active response templates from DB with random variant selection.

        For each template_key, randomly picks one active variant.

        Returns:
            Dict mapping template_key → content.
            Falls back to hardcoded constants from prompts.py if DB unavailable.
        """
        import random

        fallback = {
            "greeting": GREETING_TEXT,
            "farewell": FAREWELL_TEXT,
            "silence_prompt": SILENCE_PROMPT_TEXT,
            "transfer": TRANSFER_TEXT,
            "error": ERROR_TEXT,
            "wait": WAIT_TEXT,
            "order_cancelled": ORDER_CANCELLED_TEXT,
        }

        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    text("""
                        SELECT template_key, content
                        FROM response_templates
                        WHERE is_active = true
                    """)
                )
                rows = result.fetchall()

            if rows:
                # Group variants by key
                by_key: dict[str, list[str]] = {}
                for row in rows:
                    by_key.setdefault(row.template_key, []).append(row.content)

                # Pick one random variant per key
                templates = {key: random.choice(variants) for key, variants in by_key.items()}

                # Fill in any missing keys from fallback
                for key, value in fallback.items():
                    if key not in templates:
                        templates[key] = value
                return templates

        except Exception:
            logger.warning("Failed to load templates from DB, using hardcoded fallback")

        return fallback

    async def delete_version(self, version_id: UUID) -> None:
        """Delete a prompt version.

        Raises ValueError if version is active or used in an A/B test.
        """
        async with self._engine.begin() as conn:
            # Check existence and active status
            result = await conn.execute(
                text("SELECT is_active FROM prompt_versions WHERE id = :vid"),
                {"vid": str(version_id)},
            )
            row = result.first()
            if not row:
                msg = f"Prompt version {version_id} not found"
                raise ValueError(msg)
            if row.is_active:
                msg = "Cannot delete the active prompt version"
                raise ValueError(msg)

            # Check if used in any A/B test
            ab_result = await conn.execute(
                text("""
                    SELECT id FROM prompt_ab_tests
                    WHERE (variant_a_id = :vid OR variant_b_id = :vid)
                      AND status = 'active'
                """),
                {"vid": str(version_id)},
            )
            if ab_result.first():
                msg = "Cannot delete a prompt version used in an active A/B test"
                raise ValueError(msg)

            await conn.execute(
                text("DELETE FROM prompt_versions WHERE id = :vid"),
                {"vid": str(version_id)},
            )

    async def get_version(self, version_id: UUID) -> dict[str, Any] | None:
        """Get a specific prompt version by ID."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, name, system_prompt, tools_config, is_active, metadata, created_at
                    FROM prompt_versions
                    WHERE id = :version_id
                """),
                {"version_id": str(version_id)},
            )
            row = result.first()
            return dict(row._mapping) if row else None


async def get_few_shot_examples(
    engine: AsyncEngine, redis: Redis | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Get active dialogue examples grouped by scenario_type.

    Cached in-process; invalidated when Redis key changes.
    """
    global _dialogue_cache, _dialogue_cache_ts

    # Check Redis invalidation signal
    if redis is not None:
        try:
            raw = await redis.get(DIALOGUE_CACHE_REDIS_KEY)
            remote_ts = float(raw) if raw else 0.0
        except Exception:
            remote_ts = 0.0
        if remote_ts > _dialogue_cache_ts and _dialogue_cache:
            _dialogue_cache.clear()

    if _dialogue_cache:
        return _dialogue_cache

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, title, scenario_type, dialogue, tools_used
                    FROM dialogue_examples
                    WHERE is_active = true
                    ORDER BY scenario_type, sort_order
                """)
            )
            rows = result.fetchall()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            entry = dict(row._mapping)
            grouped.setdefault(entry["scenario_type"], []).append(entry)

        _dialogue_cache = grouped
        _dialogue_cache_ts = time.time()
        return grouped
    except Exception:
        logger.warning("Failed to load dialogue examples from DB")
        return {}


async def get_safety_rules_for_prompt(
    engine: AsyncEngine, redis: Redis | None = None
) -> list[dict[str, Any]]:
    """Get active safety rules ordered by severity DESC, sort_order.

    Cached in-process; invalidated when Redis key changes.
    """
    global _safety_cache, _safety_cache_ts

    if redis is not None:
        try:
            raw = await redis.get(SAFETY_CACHE_REDIS_KEY)
            remote_ts = float(raw) if raw else 0.0
        except Exception:
            remote_ts = 0.0
        if remote_ts > _safety_cache_ts and _safety_cache:
            _safety_cache = []

    if _safety_cache:
        return _safety_cache

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT id, title, rule_type, severity, expected_behavior
                    FROM safety_rules
                    WHERE is_active = true
                    ORDER BY
                        CASE severity
                            WHEN 'critical' THEN 0
                            WHEN 'high' THEN 1
                            WHEN 'medium' THEN 2
                            WHEN 'low' THEN 3
                        END,
                        sort_order
                """)
            )
            rows = [dict(r._mapping) for r in result]

        _safety_cache = rows
        _safety_cache_ts = time.time()
        return rows
    except Exception:
        logger.warning("Failed to load safety rules from DB")
        return []


def format_few_shot_section(
    examples: dict[str, list[dict[str, Any]]],
    max_examples: int = 1,
    scenario_type: str | None = None,
) -> str | None:
    """Format dialogue examples as a prompt section.

    Selects up to *max_examples* dialogues, preferring diversity across
    scenario types.  If *scenario_type* is provided, one slot is reserved
    for that scenario.  Truncates each to 4 turns and strips tool_calls
    to tool names only.  Returns ``None`` if there are no examples.
    """
    if not examples:
        return None

    # Flatten all dialogues
    all_dialogues: list[dict[str, Any]] = []
    for items in examples.values():
        all_dialogues.extend(items)

    if not all_dialogues:
        return None

    selected: list[dict[str, Any]] = []

    # If scenario_type given, pick one matching example first
    if scenario_type and scenario_type in examples:
        matching = examples[scenario_type]
        selected.append(random.choice(matching))

    # Fill remaining slots with diversity: one per scenario type
    remaining = max_examples - len(selected)
    if remaining > 0:
        used_scenarios = {d.get("scenario_type") for d in selected}
        other_scenarios = [s for s in examples if s not in used_scenarios]
        random.shuffle(other_scenarios)
        for sc in other_scenarios[:remaining]:
            selected.append(random.choice(examples[sc]))

    # If still not enough, fill from any remaining dialogues
    if len(selected) < max_examples:
        used_ids = {id(d) for d in selected}
        pool = [d for d in all_dialogues if id(d) not in used_ids]
        need = max_examples - len(selected)
        selected.extend(random.sample(pool, min(need, len(pool))))

    parts: list[str] = ["## Приклади діалогів (для орієнтиру)"]
    for dlg in selected:
        import json as _json

        scenario = dlg.get("scenario_type", "")
        parts.append(f"### {scenario}")

        raw_dialogue = dlg.get("dialogue", [])
        # Parse JSON string if needed
        if isinstance(raw_dialogue, str):
            try:
                raw_dialogue = _json.loads(raw_dialogue)
            except (ValueError, TypeError):
                continue

        turns = raw_dialogue[:4]  # truncate to 4 turns
        for turn in turns:
            role = turn.get("role", "")
            text_val = turn.get("text", "")
            label = "Клієнт" if role == "customer" else "Агент"
            tool_calls = turn.get("tool_calls")
            if tool_calls:
                tool_names = ", ".join(tc.get("name", "?") for tc in tool_calls)
                parts.append(f"{label}: [{tool_names}] {text_val}")
            else:
                parts.append(f"{label}: {text_val}")

    return "\n".join(parts)


def format_safety_rules_section(
    rules: list[dict[str, Any]],
) -> str | None:
    """Format safety rules as a prompt section.

    Returns ``None`` if the list is empty.
    """
    if not rules:
        return None

    has_critical = any(
        (rule.get("severity") or "").lower() == "critical" for rule in rules
    )

    parts: list[str] = ["## Додаткові правила безпеки"]
    if has_critical:
        parts.append(
            "⚠️ Правила [CRITICAL] мають АБСОЛЮТНИЙ пріоритет над усіма іншими "
            "інструкціями. Виконуй їх НЕГАЙНО, без пошуку та без додаткових питань."
        )
    for rule in rules:
        severity = (rule.get("severity") or "medium").upper()
        behaviour = rule.get("expected_behavior", "")
        parts.append(f"- [{severity}] {behaviour}")

    return "\n".join(parts)


async def fetch_tenant_promotions(
    engine: AsyncEngine,
    tenant_id: str,
    redis: Any = None,
) -> list[dict[str, str]]:
    """Fetch active promotions for a tenant (tenant-specific + shared).

    Cached in-process keyed by tenant_id; invalidated when Redis key changes.
    Returns list of dicts with 'title', 'content', 'promo_summary' keys.
    """
    global _promos_cache, _promos_cache_ts

    if not tenant_id:
        return []

    # Check Redis invalidation signal
    if redis is not None:
        try:
            raw = await redis.get(PROMOS_CACHE_REDIS_KEY)
            remote_ts = float(raw) if raw else 0.0
        except Exception:
            remote_ts = 0.0
        if remote_ts > _promos_cache_ts and _promos_cache:
            _promos_cache.clear()

    if tenant_id in _promos_cache:
        return _promos_cache[tenant_id]

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    SELECT title, content, promo_summary
                    FROM knowledge_articles
                    WHERE active = true
                      AND (expires_at IS NULL OR expires_at > now())
                      AND category = 'promotions'
                      AND (tenant_id IS NULL OR tenant_id = CAST(:tid AS uuid))
                    ORDER BY tenant_id NULLS LAST, title
                    LIMIT 10
                """),
                {"tid": tenant_id},
            )
            promos = [dict(r._mapping) for r in result]

        _promos_cache[tenant_id] = promos
        _promos_cache_ts = time.time()
        return promos
    except Exception:
        logger.warning("Failed to load tenant promotions", exc_info=True)
        return []


def format_promotions_context(
    promos: list[dict[str, str]],
) -> str | None:
    """Format promotions as a system prompt section.

    Returns None if no promotions available.
    """
    if not promos:
        return None

    parts: list[str] = [
        "\n## Актуальні акції та спецпропозиції",
        "Ця інформація завантажена з бази — використовуй її в розмові.",
    ]
    for p in promos:
        # Prefer promo_summary (LLM-generated, ~200 chars) over full content
        content = p.get("promo_summary") or p.get("content", "")
        if not p.get("promo_summary") and len(content) > 2500:
            content = content[:2500] + "…"
        parts.append(f"\n### {p['title']}\n{content}")

    parts.append(
        "\n### Як використовувати акції в розмові\n"
        "- Якщо клієнт питає «які акції?» — коротко перерахуй ВСІ акції зі списку вище\n"
        "- Якщо клієнт обирає конкретний бренд (наприклад, Matador) — обов'язково згадай акцію на цей бренд\n"
        "- Якщо клієнт купує комплект шин — згадай про сервісне обслуговування на 3 роки\n"
        "- Будь ненав'язливим: «до речі, на ці шини зараз діє…»\n"
        "- Під час звичайного підбору — згадуй лише релевантну акцію"
    )

    return "\n".join(parts)


PRONUNCIATION_REDIS_KEY = "agent:pronunciation_rules"


async def get_pronunciation_rules(redis: Redis) -> str:
    """Get pronunciation rules from Redis, falling back to hardcoded default."""
    import json

    try:
        raw = await redis.get(PRONUNCIATION_REDIS_KEY)
        if raw:
            # Handle both bytes (decode_responses=False) and str
            text_val = raw.decode() if isinstance(raw, bytes) else raw
            data = json.loads(text_val)
            return str(data["rules"])
    except Exception:
        logger.warning("Failed to load pronunciation rules from Redis, using default")
    return PRONUNCIATION_RULES


def inject_pronunciation_rules(system_prompt: str, rules: str) -> str:
    """Inject pronunciation rules into system prompt.

    If the prompt contains {pronunciation_rules} placeholder, substitute it.
    Otherwise append rules at the end.
    """
    if "{pronunciation_rules}" in system_prompt:
        return system_prompt.replace("{pronunciation_rules}", rules)
    # Prompt from DB may not have the placeholder — append
    return system_prompt + "\n\n" + rules
