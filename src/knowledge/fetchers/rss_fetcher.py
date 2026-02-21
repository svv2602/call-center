"""RSS feed fetcher — uses feedparser + trafilatura for content extraction."""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser  # type: ignore[import-untyped]
import trafilatura

from src.knowledge.scraper import ScrapedArticle

logger = logging.getLogger(__name__)

_USER_AGENT = "CallCenterAI-Scraper/1.0 (+https://github.com/call-center-ai; polite bot)"


class RSSFetcher:
    """Fetcher for RSS feeds with content extraction via trafilatura."""

    def __init__(
        self,
        feed_url: str,
        request_delay: float = 2.0,
        title_filter_regex: str | None = None,
    ) -> None:
        self._feed_url = feed_url
        self._request_delay = request_delay
        self._title_filter: re.Pattern[str] | None = None
        if title_filter_regex:
            self._title_filter = re.compile(title_filter_regex)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30.0),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml",
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
        """Parse RSS feed and return matching entries."""
        assert self._session is not None, "Call open() first"

        try:
            async with self._session.get(self._feed_url) as resp:
                if resp.status != 200:
                    logger.warning("RSS feed %s returned %d", self._feed_url, resp.status)
                    return []
                xml_content = await resp.text()
        except Exception:
            logger.exception("Failed to fetch RSS feed %s", self._feed_url)
            return []

        feed = feedparser.parse(xml_content)
        articles: list[dict[str, str]] = []

        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            if not link:
                continue

            # Apply title filter
            if self._title_filter and not self._title_filter.search(title):
                continue

            # Parse published date
            published = ""
            pub_raw = entry.get("published") or entry.get("updated", "")
            if pub_raw:
                published = _parse_rss_date(pub_raw)

            # Apply min_date filter
            if min_date and published:
                try:
                    pub_date = datetime.date.fromisoformat(published)
                    if pub_date < min_date:
                        continue
                except ValueError:
                    pass

            articles.append(
                {
                    "url": link,
                    "title": title,
                    "published": published,
                }
            )

            if len(articles) >= max_articles:
                break

        logger.info("RSS feed %s: %d entries matched", self._feed_url, len(articles))
        return articles

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

        # Extract title from HTML as trafilatura may not always return it
        title = _extract_title(html) or url.split("/")[-1]

        return ScrapedArticle(
            url=url,
            title=title,
            content=content,
            category="comparisons",  # tire tests → comparisons
            published=published,
        )


def _parse_rss_date(date_str: str) -> str:
    """Parse RSS date formats → ISO YYYY-MM-DD."""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.date().isoformat()
    except Exception:
        pass
    # Try ISO format
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(date_str[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _extract_title(html: str) -> str:
    """Extract title from HTML using trafilatura metadata or simple parsing."""
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        return metadata.title
    # Fallback: find <title> tag
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""
