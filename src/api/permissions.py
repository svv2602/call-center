"""Granular permission constants and role defaults.

Permissions follow the `resource:action` convention.
Roles get default permissions, but users can have custom overrides
via the `permissions` JSONB column on `admin_users`.
"""

from __future__ import annotations

# ── All known permissions ────────────────────────────────────

CONTENT_PERMISSIONS = [
    "sandbox:read", "sandbox:write", "sandbox:delete",
    "knowledge:read", "knowledge:write", "knowledge:delete",
    "scraper:read", "scraper:write", "scraper:delete", "scraper:execute",
    "training:read", "training:write", "training:delete", "training:execute",
    "prompts:read", "prompts:write", "prompts:delete",
]

SYSTEM_PERMISSIONS = [
    "users:read", "users:write",
    "audit:read",
    "tenants:read", "tenants:write", "tenants:delete",
    "operators:read", "operators:write",
    "analytics:read", "analytics:export",
    "llm_config:read", "llm_config:write",
    "notifications:read", "notifications:write",
    "system:read", "system:write",
    "vehicles:read", "vehicles:write",
    "pronunciation:read", "pronunciation:write",
]

ALL_PERMISSIONS: list[str] = sorted({*CONTENT_PERMISSIONS, *SYSTEM_PERMISSIONS})

# ── Role → default permissions ───────────────────────────────

ROLE_DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "admin": ["*"],
    "analyst": [
        "analytics:read", "analytics:export",
        "knowledge:read",
        "training:read",
        "prompts:read",
        "vehicles:read",
        "operators:read",
    ],
    "operator": [
        "operators:read",
    ],
    "content_manager": CONTENT_PERMISSIONS.copy(),
}

# ── Permission groups (for UI rendering) ─────────────────────

PERMISSION_GROUPS: dict[str, list[str]] = {
    "sandbox": ["sandbox:read", "sandbox:write", "sandbox:delete"],
    "knowledge": ["knowledge:read", "knowledge:write", "knowledge:delete"],
    "scraper": ["scraper:read", "scraper:write", "scraper:delete", "scraper:execute"],
    "training": ["training:read", "training:write", "training:delete", "training:execute"],
    "prompts": ["prompts:read", "prompts:write", "prompts:delete"],
    "users": ["users:read", "users:write"],
    "audit": ["audit:read"],
    "tenants": ["tenants:read", "tenants:write", "tenants:delete"],
    "operators": ["operators:read", "operators:write"],
    "analytics": ["analytics:read", "analytics:export"],
    "llm_config": ["llm_config:read", "llm_config:write"],
    "notifications": ["notifications:read", "notifications:write"],
    "system": ["system:read", "system:write"],
    "vehicles": ["vehicles:read", "vehicles:write"],
    "pronunciation": ["pronunciation:read", "pronunciation:write"],
}
