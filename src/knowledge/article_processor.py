"""LLM-based article processor for scraped content.

Uses llm_complete helper (router → Anthropic fallback) to clean up scraped articles:
- Remove marketing/promotional content
- Classify as useful or skip
- Pick KB category
- Return structured JSON
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.llm.helpers import llm_complete
from src.llm.models import LLMTask

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 8000

_LANGUAGE_NAMES: dict[str, str] = {
    "de": "German (Deutsch)",
    "en": "English",
    "fr": "French (Français)",
    "pl": "Polish (Polski)",
}

_SYSTEM_PROMPT = """You are a content processor for a Ukrainian tire shop knowledge base.
Your task is to clean up a scraped article and decide if it's useful for customer service agents.

Rules:
1. Remove all marketing text, calls-to-action, product links, promotional blocks, ads
2. Keep factual tire knowledge: comparisons, specifications, tables, maintenance tips, guides
3. Preserve headings (##), lists (-), and tables (| ... |)
4. If the article is purely promotional or has no useful factual content, mark it as not useful
5. IMPORTANT: Keep the article in Ukrainian (українською мовою). Do NOT translate to English or any other language. The knowledge base serves Ukrainian-speaking customers.

Respond ONLY with valid JSON (no markdown fences):
{
  "is_useful": true/false,
  "skip_reason": "reason if not useful, null otherwise",
  "title": "cleaned article title in Ukrainian",
  "category": "one of: brands, guides, faq, comparisons, policies, procedures, returns, warranty, delivery, promotions, news, general",
  "content": "cleaned article content in Ukrainian markdown"
}"""

_PROMOTION_SYSTEM_PROMPT = """You are a content processor for a Ukrainian tire shop knowledge base.
Your task is to clean up a scraped PROMOTION page from the shop's own website.
This is the shop's current active promotion — the customer service agent MUST know about it to inform callers.

Rules:
1. This IS useful content — promotions from our own shop are always useful for the agent
2. Extract the key facts: what is the offer, which products/brands, discount amount, conditions, duration
3. Remove product catalog listings (individual SKUs, prices, "add to cart"), keep only the summary of what's on offer
4. Remove navigation, sorting controls, pagination
5. Preserve headings (##), lists (-), and tables (| ... |)
6. IMPORTANT: Keep the article in Ukrainian (українською мовою)

Respond ONLY with valid JSON (no markdown fences):
{
  "is_useful": true,
  "skip_reason": null,
  "title": "short descriptive title of the promotion in Ukrainian",
  "category": "promotions",
  "content": "cleaned promotion summary in Ukrainian markdown"
}"""

_SHOP_INFO_SYSTEM_PROMPT = """You are a content processor for a Ukrainian tire shop knowledge base.
Your task is to clean up a page from the shop's own website. This page was explicitly added by the administrator
as important reference information for the customer service agent.

Rules:
1. This IS useful content — the admin added this page deliberately, so it must always be included
2. Clean up navigation elements, headers/footers, cookie notices, and other UI chrome
3. Preserve all factual content: contact details, addresses, phone numbers, working hours, legal terms, policies, procedures
4. Preserve headings (##), lists (-), and tables (| ... |)
5. IMPORTANT: Keep the article in Ukrainian (українською мовою). Do NOT translate to English or any other language.

Respond ONLY with valid JSON (no markdown fences):
{
  "is_useful": true,
  "skip_reason": null,
  "title": "cleaned article title in Ukrainian",
  "category": "one of: brands, guides, faq, comparisons, policies, procedures, returns, warranty, delivery, promotions, news, general",
  "content": "cleaned article content in Ukrainian markdown"
}"""

_TRANSLATION_ADDENDUM = """
6. The source article is in {language_name}. Translate ALL content to Ukrainian (українською мовою). Preserve technical tire terminology (sizes, specifications). Keep brand names in their original form."""


@dataclass
class ProcessedArticle:
    """Result of LLM article processing."""

    is_useful: bool
    skip_reason: str | None
    title: str
    category: str
    content: str


async def process_article(
    title: str,
    content: str,
    source_url: str,
    llm_router: object | None = None,
    source_language: str = "uk",
    is_promotion: bool = False,
    is_shop_info: bool = False,
) -> ProcessedArticle:
    """Process a scraped article through LLM for cleanup and classification.

    Args:
        title: Original article title.
        content: Raw scraped content.
        source_url: Source URL for context.
        llm_router: Optional LLMRouter for multi-provider routing.
        source_language: ISO language code of the source article (e.g. 'uk', 'de', 'en').
            When not 'uk', adds translation instructions to the prompt.
        is_promotion: If True, use promotion-specific prompt that always marks content as useful.
        is_shop_info: If True, use shop-info prompt (admin-added pages, always useful).

    Returns:
        ProcessedArticle with cleaned content and classification.
    """
    # Truncate to avoid excessive token usage
    truncated = content[:_MAX_CONTENT_CHARS]
    if len(content) > _MAX_CONTENT_CHARS:
        truncated += "\n\n[... content truncated ...]"

    # Build system prompt
    if is_promotion:
        system_prompt = _PROMOTION_SYSTEM_PROMPT
    elif is_shop_info:
        system_prompt = _SHOP_INFO_SYSTEM_PROMPT
    else:
        system_prompt = _SYSTEM_PROMPT
    if not is_promotion and not is_shop_info and source_language and source_language != "uk":
        language_name = _LANGUAGE_NAMES.get(source_language, source_language)
        system_prompt += _TRANSLATION_ADDENDUM.format(language_name=language_name)

    user_message = f"""Article URL: {source_url}
Title: {title}

Content:
{truncated}"""

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    try:
        # Only pass router explicitly when it's not None;
        # when None, let llm_complete use its sentinel default
        # to trigger lazy router initialization in Celery workers.
        llm_kwargs: dict[str, Any] = {"system": system_prompt, "max_tokens": 4096}
        if llm_router is not None:
            llm_kwargs["router"] = llm_router
        raw_text = await llm_complete(
            LLMTask.ARTICLE_PROCESSOR,
            messages,
            **llm_kwargs,
        )

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

        data = json.loads(raw_text)

        return ProcessedArticle(
            is_useful=bool(data.get("is_useful", False)),
            skip_reason=data.get("skip_reason"),
            title=data.get("title", title),
            category=_validate_category(data.get("category", "general")),
            content=data.get("content", ""),
        )

    except json.JSONDecodeError:
        logger.exception("LLM returned invalid JSON for article: %s", source_url)
        # Fallback: treat as useful with original content
        return ProcessedArticle(
            is_useful=True,
            skip_reason=None,
            title=title,
            category="general",
            content=content[:_MAX_CONTENT_CHARS],
        )

    except Exception:
        logger.exception("LLM processing failed for article: %s", source_url)
        raise


_VALID_CATEGORIES = {
    "brands",
    "guides",
    "faq",
    "comparisons",
    "policies",
    "procedures",
    "returns",
    "warranty",
    "delivery",
    "general",
    "promotions",
    "news",
}


def _validate_category(category: str | None) -> str:
    """Validate and normalize category value."""
    if not category:
        return "general"
    cat = category.lower().strip()
    return cat if cat in _VALID_CATEGORIES else "general"
