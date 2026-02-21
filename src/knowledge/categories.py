"""Knowledge base article categories — single source of truth.

All components (API, tools, parsers) import categories from here.
"""

from __future__ import annotations

CATEGORIES: list[dict[str, str]] = [
    {"value": "brands", "label": "Brands"},
    {"value": "guides", "label": "Guides"},
    {"value": "faq", "label": "FAQ"},
    {"value": "comparisons", "label": "Comparisons"},
    {"value": "policies", "label": "Policies"},
    {"value": "procedures", "label": "Procedures"},
    {"value": "returns", "label": "Returns"},
    {"value": "warranty", "label": "Warranty"},
    {"value": "delivery", "label": "Delivery"},
    {"value": "promotions", "label": "Promotions"},
    {"value": "general", "label": "General"},
]

CATEGORY_VALUES: list[str] = [c["value"] for c in CATEGORIES]


def is_valid_category(value: str) -> bool:
    """Check if a category value is valid."""
    return value in CATEGORY_VALUES
