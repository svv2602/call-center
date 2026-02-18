"""LLM-based article processor for scraped content.

Uses Claude (Haiku) to clean up scraped articles:
- Remove marketing/promotional content
- Classify as useful or skip
- Pick KB category
- Return structured JSON
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 8000

_SYSTEM_PROMPT = """You are a content processor for a tire shop knowledge base.
Your task is to clean up a scraped article and decide if it's useful for customer service agents.

Rules:
1. Remove all marketing text, calls-to-action, product links, promotional blocks, ads
2. Keep factual tire knowledge: comparisons, specifications, tables, maintenance tips, guides
3. Preserve headings (##), lists (-), and tables (| ... |)
4. If the article is purely promotional or has no useful factual content, mark it as not useful

Respond ONLY with valid JSON (no markdown fences):
{
  "is_useful": true/false,
  "skip_reason": "reason if not useful, null otherwise",
  "title": "cleaned article title",
  "category": "one of: brands, guides, faq, comparisons, policies, procedures, returns, warranty, delivery, general",
  "content": "cleaned article content in markdown"
}"""


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
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
) -> ProcessedArticle:
    """Process a scraped article through LLM for cleanup and classification.

    Args:
        title: Original article title.
        content: Raw scraped content.
        source_url: Source URL for context.
        api_key: Anthropic API key.
        model: Model to use (default: Haiku for cost efficiency).

    Returns:
        ProcessedArticle with cleaned content and classification.
    """
    # Truncate to avoid excessive token usage
    truncated = content[:_MAX_CONTENT_CHARS]
    if len(content) > _MAX_CONTENT_CHARS:
        truncated += "\n\n[... content truncated ...]"

    user_message = f"""Article URL: {source_url}
Title: {title}

Content:
{truncated}"""

    client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()

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
}


def _validate_category(category: str | None) -> str:
    """Validate and normalize category value."""
    if not category:
        return "general"
    cat = category.lower().strip()
    return cat if cat in _VALID_CATEGORIES else "general"
