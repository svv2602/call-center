"""Generic HTML fetcher — discovers articles from listing pages, extracts via trafilatura."""

from __future__ import annotations

import asyncio
import datetime  # noqa: TC003 - used at runtime in method signatures
import logging
from urllib.parse import urljoin, urlparse

import aiohttp
import trafilatura  # type: ignore[import-untyped]
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

from src.knowledge.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

_USER_AGENT = "CallCenterAI-Scraper/1.0 (+https://github.com/call-center-ai; polite bot)"


class GenericHTMLFetcher:
    """Fetcher for generic HTML sites using listing pages + trafilatura."""

    def __init__(
        self,
        base_url: str,
        request_delay: float = 2.0,
        listing_urls: list[str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._base_domain = urlparse(base_url).netloc
        self._request_delay = request_delay
        self._listing_urls = listing_urls or []
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30.0),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
            },
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def discover_articles(
        self,
        *,
        max_articles: int = 20,
        min_date: datetime.date | None = None,
    ) -> list[dict[str, str]]:
        """Fetch listing pages and discover article links."""
        assert self._session is not None, "Call open() first"

        articles: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for listing_url in self._listing_urls:
            try:
                async with self._session.get(listing_url) as resp:
                    if resp.status != 200:
                        logger.warning("Listing page %s returned %d", listing_url, resp.status)
                        continue
                    html = await resp.text()
            except Exception:
                logger.exception("Failed to fetch listing page %s", listing_url)
                continue

            page_articles = self._extract_links(html, listing_url)

            for item in page_articles:
                url = item["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                articles.append(item)
                if len(articles) >= max_articles:
                    break

            if len(articles) >= max_articles:
                break

            await asyncio.sleep(self._request_delay)

        logger.info(
            "Generic HTML %s: discovered %d articles from %d listing pages",
            self._base_url,
            len(articles),
            len(self._listing_urls),
        )
        return articles

    def _extract_links(self, html: str, listing_url: str) -> list[dict[str, str]]:
        """Extract article links from a listing page HTML."""
        soup = BeautifulSoup(html, "lxml")
        results: list[dict[str, str]] = []
        listing_parsed = urlparse(listing_url)

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Resolve relative URLs
            full_url = urljoin(listing_url, href)
            parsed = urlparse(full_url)

            # Must be same domain or subdomain
            if not self._is_same_site(parsed.netloc):
                continue

            # Skip listing page itself, anchors, non-article paths
            if full_url.rstrip("/") == listing_url.rstrip("/"):
                continue

            # Must be deeper than the listing URL path (i.e., an article, not nav)
            if len(parsed.path.rstrip("/")) <= len(listing_parsed.path.rstrip("/")):
                continue

            # Skip common non-article patterns
            if _is_non_article_url(parsed.path):
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                # Try to get title from nested elements
                heading = link.find(["h2", "h3", "h4", "span"])
                if heading:
                    title = heading.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            results.append(
                {
                    "url": full_url,
                    "title": title,
                    "published": "",
                }
            )

        return results

    def _is_same_site(self, netloc: str) -> bool:
        """Check if netloc belongs to the same site (domain or subdomain)."""
        return netloc == self._base_domain or netloc.endswith("." + self._base_domain)

    async def fetch_article(self, url: str, published: str | None = None) -> ScrapedArticle | None:
        """Fetch article page and extract content via trafilatura."""
        assert self._session is not None, "Call open() first"

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("Article %s returned %d", url, resp.status)
                    return None
                html = await resp.text()
        except Exception:
            logger.exception("Failed to fetch article %s", url)
            return None

        await asyncio.sleep(self._request_delay)

        content = trafilatura.extract(
            html,
            output_format="txt",
            include_tables=True,
            include_links=False,
            favor_recall=True,
        )

        if not content or len(content.strip()) < 100:
            logger.warning("Article %s has too little content, skipping", url)
            return None

        # Extract title
        title = _extract_title(html) or url.split("/")[-1]

        return ScrapedArticle(
            url=url,
            title=title,
            content=content,
            category="comparisons",  # external tire tests → comparisons
            published=published,
        )


def _is_non_article_url(path: str) -> bool:
    """Check if URL path looks like a non-article page."""
    path_lower = path.lower()
    skip_patterns = (
        "/login",
        "/register",
        "/cart",
        "/checkout",
        "/search",
        "/impressum",
        "/datenschutz",
        "/privacy",
        "/cookie",
        "/kontakt",
        "/contact",
        "/sitemap",
        ".pdf",
        ".jpg",
        ".png",
    )
    return any(pat in path_lower for pat in skip_patterns)


def _extract_title(html: str) -> str:
    """Extract title from HTML using trafilatura metadata."""
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        return metadata.title
    # Fallback
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""
