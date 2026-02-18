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
  "category": "one of: brands, guides, faq, comparisons, policies, procedures, returns, warranty, delivery, general",
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
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    llm_router: object | None = None,
    source_language: str = "uk",
) -> ProcessedArticle:
    """Process a scraped article through LLM for cleanup and classification.

    Args:
        title: Original article title.
        content: Raw scraped content.
        source_url: Source URL for context.
        api_key: Anthropic API key.
        model: Model to use (default: Haiku for cost efficiency).
        llm_router: Optional LLMRouter for multi-provider routing.
        source_language: ISO language code of the source article (e.g. 'uk', 'de', 'en').
            When not 'uk', adds translation instructions to the prompt.

    Returns:
        ProcessedArticle with cleaned content and classification.
    """
    # Truncate to avoid excessive token usage
    truncated = content[:_MAX_CONTENT_CHARS]
    if len(content) > _MAX_CONTENT_CHARS:
        truncated += "\n\n[... content truncated ...]"

    # Build system prompt with optional translation addendum
    system_prompt = _SYSTEM_PROMPT
    if source_language and source_language != "uk":
        language_name = _LANGUAGE_NAMES.get(source_language, source_language)
        system_prompt += _TRANSLATION_ADDENDUM.format(language_name=language_name)

    user_message = f"""Article URL: {source_url}
Title: {title}

Content:
{truncated}"""

    messages = [{"role": "user", "content": user_message}]

    try:
        if llm_router is not None:
            from src.llm.models import LLMTask

            llm_response = await llm_router.complete(  # type: ignore[union-attr]
                LLMTask.ARTICLE_PROCESSOR,
                messages,
                system=system_prompt,
                max_tokens=4096,
            )
            raw_text = llm_response.text.strip()
        else:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
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
