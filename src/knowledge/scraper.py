"""HTML scraper for prokoleso.ua articles.

Discovers article URLs from listing pages and fetches/parses individual articles.
Follows polite crawling conventions: User-Agent, request delay, timeouts.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import re
from dataclasses import dataclass, field
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

# Static page URL patterns → KB category mapping
_STATIC_PAGE_CATEGORY_MAP: dict[str, str] = {
    "oplata-i-dostavka": "delivery",
    "dostavka": "delivery",
    "oplata": "delivery",
    "garantiya": "warranty",
    "vozvrat": "returns",
    "promotions": "policies",
    "akcii": "policies",
    "polzovatelskoe-soglashenie": "policies",
    "kontakty": "general",
}

_DEFAULT_CATEGORY = "general"


def content_hash(text: str) -> str:
    """Compute SHA-256 hash of content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

_USER_AGENT = "CallCenterAI-Scraper/1.0 (+https://github.com/call-center-ai; polite bot)"


def _parse_date_text(text: str) -> str:
    """Parse date text like '20.09.2024' or '2024-09-20' → ISO format 'YYYY-MM-DD'.

    Returns empty string if parsing fails.
    """
    text = text.strip()
    # DD.MM.YYYY format (common on Ukrainian sites)
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(year, month, day).isoformat()
        except ValueError:
            return ""
    # Already ISO format
    m = re.match(r"\d{4}-\d{2}-\d{2}", text)
    if m:
        try:
            datetime.date.fromisoformat(m.group(0))
            return m.group(0)
        except ValueError:
            return ""
    return ""


@dataclass
class ScrapedArticle:
    """Raw scraped article data."""

    url: str
    title: str
    content: str
    category: str
    published: str | None = field(default=None)


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
        self,
        info_path: str = "/ua/info/",
        max_pages: int = 3,
        min_date: datetime.date | None = None,
        max_date: datetime.date | None = None,
    ) -> list[dict[str, str]]:
        """Crawl listing pages and extract article URLs.

        Args:
            info_path: Listing path on the site.
            max_pages: Maximum number of pages to crawl.
            min_date: If set, skip articles older than this date.
                When all articles on a page are older, stop early.
            max_date: If set, skip articles newer than this date.

        Returns list of {url, title, published} dicts.
        """
        assert self._session is not None, "Call open() before using the scraper"

        articles: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for page in range(1, max_pages + 1):
            page_url = urljoin(self._base_url, info_path)
            if page > 1:
                page_url = page_url.rstrip("/") + f"?page={page}"

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

            # Apply date range filter if set
            all_too_old = True
            for item in page_articles:
                url = item["url"]
                if url in seen_urls:
                    continue

                pub_str = item.get("published")
                if pub_str and (min_date or max_date):
                    try:
                        pub = datetime.date.fromisoformat(pub_str)
                        if min_date and pub < min_date:
                            continue
                        if max_date and pub > max_date:
                            # Too new — skip but don't trigger early-stop
                            all_too_old = False
                            continue
                        all_too_old = False
                    except ValueError:
                        all_too_old = False  # can't parse → include it
                else:
                    all_too_old = False

                seen_urls.add(url)
                articles.append(item)

            logger.info(
                "Page %d: found %d articles (total: %d)",
                page,
                len(page_articles),
                len(articles),
            )

            # Listing is newest-first: if all articles on page are too old, stop
            if min_date and all_too_old:
                logger.info(
                    "All articles on page %d are older than %s, stopping",
                    page,
                    min_date.isoformat(),
                )
                break

            if page < max_pages:
                await asyncio.sleep(self._request_delay)

        return articles

    def _extract_article_links(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        """Extract article links from a listing page.

        Returns list of {url, title, published} dicts.
        ``published`` is ISO date string (YYYY-MM-DD) or empty string.
        """
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

            # Extract date from article-date element (inside the card or nearby)
            published = ""
            date_el = link.select_one(".article-date")
            if date_el is None:
                # Also look for .date class
                date_el = link.select_one(".date")
            if date_el:
                published = _parse_date_text(date_el.get_text(strip=True))

            if title and len(title) > 5:
                results.append({"url": url, "title": title, "published": published})

        return results

    async def fetch_article(self, url: str, published: str | None = None) -> ScrapedArticle | None:
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
            published=published or None,
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
        url_lower = url.lower()
        for slug, category in _SLUG_CATEGORY_MAP.items():
            if slug in url_lower:
                return category
        for slug, category in _STATIC_PAGE_CATEGORY_MAP.items():
            if slug in url_lower:
                return category
        return _DEFAULT_CATEGORY
