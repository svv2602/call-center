"""HTML scraper for prokoleso.ua articles.

Discovers article URLs from listing pages and fetches/parses individual articles.
Follows polite crawling conventions: User-Agent, request delay, timeouts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# URL slug → KB category mapping
_SLUG_CATEGORY_MAP: dict[str, str] = {
    "novosti": "general",
    "testy-i-obzory-shin": "comparisons",
    "vse-o-shinah": "guides",
    "vse-o-diskah": "guides",
    "shinnye-kalkulyatory": "guides",
    "pokupателю": "faq",
    "pokupatelu": "faq",
}

_DEFAULT_CATEGORY = "general"

_USER_AGENT = "CallCenterAI-Scraper/1.0 (+https://github.com/call-center-ai; polite bot)"


@dataclass
class ScrapedArticle:
    """Raw scraped article data."""

    url: str
    title: str
    content: str
    category: str


class ProKolesoScraper:
    """Scraper for prokoleso.ua info articles."""

    def __init__(
        self,
        base_url: str = "https://prokoleso.ua",
        request_delay: float = 2.0,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_delay = request_delay
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "uk-UA,uk;q=0.9",
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def discover_article_urls(
        self, info_path: str = "/ua/info/", max_pages: int = 3
    ) -> list[dict[str, str]]:
        """Crawl listing pages and extract article URLs.

        Returns list of {url, title} dicts.
        """
        assert self._session is not None, "Call open() before using the scraper"

        articles: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            page_url = urljoin(self._base_url, info_path)
            if page > 1:
                page_url = page_url.rstrip("/") + f"/page/{page}/"

            try:
                async with self._session.get(page_url) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Listing page %s returned %d, stopping discovery",
                            page_url,
                            resp.status,
                        )
                        break
                    html = await resp.text()
            except Exception:
                logger.exception("Failed to fetch listing page %s", page_url)
                break

            soup = BeautifulSoup(html, "lxml")
            page_articles = self._extract_article_links(soup)

            if not page_articles:
                logger.info("No articles found on page %d, stopping", page)
                break

            for item in page_articles:
                url = item["url"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    articles.append(item)

            logger.info(
                "Page %d: found %d articles (total: %d)",
                page,
                len(page_articles),
                len(articles),
            )

            if page < max_pages:
                await asyncio.sleep(self._request_delay)

        return articles

    def _extract_article_links(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        """Extract article links from a listing page."""
        results: list[dict[str, str]] = []

        # prokoleso.ua uses article cards with links — try common patterns
        for link in soup.select("a[href*='/ua/info/']"):
            href = link.get("href", "")
            if not href or href.endswith("/ua/info/") or "/page/" in href:
                continue

            url = href if href.startswith("http") else urljoin(self._base_url, href)

            # Extract title from link text or nested heading
            title_el = link.find(["h2", "h3", "h4"])
            title = title_el.get_text(strip=True) if title_el else link.get_text(strip=True)

            if title and len(title) > 5:
                results.append({"url": url, "title": title})

        return results

    async def fetch_article(self, url: str) -> ScrapedArticle | None:
        """Fetch and parse a single article page."""
        assert self._session is not None, "Call open() before using the scraper"

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("Article %s returned %d", url, resp.status)
                    return None
                html = await resp.text()
        except Exception:
            logger.exception("Failed to fetch article %s", url)
            return None

        title, content = self._parse_article_content(html)
        if not content or len(content.strip()) < 100:
            logger.warning("Article %s has too little content, skipping", url)
            return None

        category = self._extract_category(url)

        return ScrapedArticle(
            url=url,
            title=title,
            content=content,
            category=category,
        )

    def _parse_article_content(self, html: str) -> tuple[str, str]:
        """Extract title and clean text from article HTML."""
        soup = BeautifulSoup(html, "lxml")

        # Remove scripts, styles, nav, footer, ads
        for tag in soup.find_all(["script", "style", "nav", "footer", "iframe", "noscript"]):
            tag.decompose()

        # Try to find the main content area
        content_el = (
            soup.find(id="article-content")
            or soup.find("article")
            or soup.find(class_=re.compile(r"article|post|content|entry", re.IGNORECASE))
            or soup.find("main")
        )

        # Extract title
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else "Без названия"

        if content_el is None:
            # Fallback: use body
            content_el = soup.find("body")
            if content_el is None:
                return title, ""

        # Remove promotional elements
        for promo in content_el.find_all(
            class_=re.compile(
                r"promo|banner|advert|sidebar|widget|share|social|related", re.IGNORECASE
            )
        ):
            promo.decompose()

        # Remove product cards/links
        for product in content_el.find_all(
            class_=re.compile(r"product|buy|cart|price|shop", re.IGNORECASE)
        ):
            product.decompose()

        # Convert to markdown-like text
        text = self._html_to_markdown(content_el)
        return title, text

    def _html_to_markdown(self, el: BeautifulSoup) -> str:
        """Convert HTML element to simplified markdown text."""
        lines: list[str] = []

        for child in el.descendants:
            if child.name in ("h2", "h3", "h4"):
                prefix = "#" * (int(child.name[1]))
                heading_text = child.get_text(strip=True)
                if heading_text:
                    lines.append(f"\n{prefix} {heading_text}\n")
            elif child.name == "li":
                li_text = child.get_text(strip=True)
                if li_text:
                    lines.append(f"- {li_text}")
            elif child.name == "p":
                p_text = child.get_text(strip=True)
                if p_text:
                    lines.append(f"\n{p_text}\n")
            elif child.name == "tr":
                cells = [td.get_text(strip=True) for td in child.find_all(["td", "th"])]
                if cells:
                    lines.append("| " + " | ".join(cells) + " |")

        text = "\n".join(lines)
        # Clean up excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _extract_category(self, url: str) -> str:
        """Map URL slug to KB category."""
        for slug, category in _SLUG_CATEGORY_MAP.items():
            if slug in url.lower():
                return category
        return _DEFAULT_CATEGORY
