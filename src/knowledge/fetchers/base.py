"""Base protocol and factory for content fetchers."""

from __future__ import annotations

import datetime  # noqa: TC003 - used at runtime in Protocol method signatures
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.knowledge.scraper import ScrapedArticle


@runtime_checkable
class ContentFetcher(Protocol):
    """Protocol for content source fetchers."""

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def discover_articles(
        self,
        *,
        max_articles: int = 20,
        min_date: datetime.date | None = None,
    ) -> list[dict[str, str]]:
        """Discover article URLs from the source.

        Returns list of dicts with keys: url, title, published (optional).
        """
        ...

    async def fetch_article(self, url: str, published: str | None = None) -> ScrapedArticle | None:
        """Fetch and parse a single article page."""
        ...


def create_fetcher(source_config: dict[str, Any]) -> ContentFetcher:
    """Create appropriate fetcher based on source_type.

    Args:
        source_config: Dict with at least 'source_type', 'source_url',
                       'request_delay', and 'settings' keys.

    Returns:
        ContentFetcher implementation.

    Raises:
        ValueError: If source_type is unknown.
    """
    source_type = source_config["source_type"]
    source_url = source_config["source_url"]
    request_delay = source_config.get("request_delay", 2.0)
    settings = source_config.get("settings", {})

    if source_type == "prokoleso":
        from src.knowledge.fetchers.prokoleso import ProKolesoFetcher

        return ProKolesoFetcher(
            base_url=source_url,
            request_delay=request_delay,
            info_path=settings.get("info_path", "/ua/info/"),
            max_pages=settings.get("max_pages", 3),
        )

    if source_type == "rss":
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        return RSSFetcher(
            feed_url=source_url,
            request_delay=request_delay,
            title_filter_regex=settings.get("title_filter_regex"),
        )

    if source_type == "generic_html":
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        return GenericHTMLFetcher(
            base_url=source_url,
            request_delay=request_delay,
            listing_urls=settings.get("listing_urls", []),
        )

    raise ValueError(f"Unknown source_type: {source_type}")
