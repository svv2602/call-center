"""ProKoleso fetcher — thin wrapper around existing ProKolesoScraper."""

from __future__ import annotations

import datetime  # noqa: TC003 - used at runtime in method signatures

from src.knowledge.scraper import ProKolesoScraper, ScrapedArticle


class ProKolesoFetcher:
    """Fetcher for prokoleso.ua, delegates to ProKolesoScraper."""

    def __init__(
        self,
        base_url: str = "https://prokoleso.ua",
        request_delay: float = 2.0,
        info_path: str = "/ua/info/",
        max_pages: int = 3,
    ) -> None:
        self._scraper = ProKolesoScraper(
            base_url=base_url,
            request_delay=request_delay,
        )
        self._info_path = info_path
        self._max_pages = max_pages

    async def open(self) -> None:
        await self._scraper.open()

    async def close(self) -> None:
        await self._scraper.close()

    async def discover_articles(
        self,
        *,
        max_articles: int = 20,
        min_date: datetime.date | None = None,
    ) -> list[dict[str, str]]:
        articles = await self._scraper.discover_article_urls(
            info_path=self._info_path,
            max_pages=self._max_pages,
            min_date=min_date,
        )
        return articles[:max_articles]

    async def fetch_article(
        self, url: str, published: str | None = None
    ) -> ScrapedArticle | None:
        return await self._scraper.fetch_article(url, published=published)
