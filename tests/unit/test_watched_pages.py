"""Unit tests for watched pages feature — API endpoints, content hashing, rescrape logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── content_hash ────────────────────────────────────────────


class TestContentHash:
    """Test SHA-256 content hashing."""

    def test_consistent_hashing(self) -> None:
        from src.knowledge.scraper import content_hash

        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_different_content_different_hash(self) -> None:
        from src.knowledge.scraper import content_hash

        h1 = content_hash("content version 1")
        h2 = content_hash("content version 2")
        assert h1 != h2

    def test_empty_string(self) -> None:
        from src.knowledge.scraper import content_hash

        h = content_hash("")
        assert len(h) == 64

    def test_unicode_content(self) -> None:
        from src.knowledge.scraper import content_hash

        h = content_hash("Доставка та оплата — українською мовою")
        assert len(h) == 64


# ─── Static page category mapping ───────────────────────────


class TestStaticPageCategory:
    """Test URL → category mapping for static pages."""

    def test_delivery_page(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/oplata-i-dostavka/") == "delivery"

    def test_warranty_page(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/garantiya/") == "warranty"

    def test_returns_page(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/vozvrat/") == "returns"

    def test_promotions_page(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/promotions/") == "policies"

    def test_user_agreement(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/polzovatelskoe-soglashenie/") == "policies"

    def test_contacts_page(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/kontakty/") == "general"

    def test_unknown_page_defaults_to_general(self) -> None:
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/some-unknown-page/") == "general"

    def test_article_slug_still_works(self) -> None:
        """Existing article slug mapping should still work."""
        from src.knowledge.scraper import ProKolesoScraper

        scraper = ProKolesoScraper()
        assert scraper._extract_category("https://prokoleso.ua/ua/info/testy-i-obzory-shin/some-article") == "comparisons"


# ─── API endpoint tests (via mocking) ───────────────────────


class AsyncContextManagerMock:
    """Helper to mock async context managers (async with)."""

    def __init__(self, return_value: AsyncMock) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> AsyncMock:
        return self._return_value

    async def __aexit__(self, *args: object) -> None:
        pass


def _make_engine_mock(query_result: MagicMock | None = None) -> MagicMock:
    """Create a mock engine with begin() returning an async context manager."""
    mock_conn = AsyncMock()
    if query_result is not None:
        mock_conn.execute = AsyncMock(return_value=query_result)
    else:
        mock_conn.execute = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
    return mock_engine


class TestWatchedPagesAPI:
    """Test watched pages API validation logic."""

    @pytest.mark.asyncio
    async def test_add_watched_page_validates_url(self) -> None:
        """URL must start with http — Pydantic rejects invalid values."""
        from pydantic import ValidationError

        from src.api.scraper import WatchedPageCreate

        with pytest.raises(ValidationError):
            WatchedPageCreate(url="not-a-url", category="delivery", rescrape_interval_hours=24)

    @pytest.mark.asyncio
    async def test_add_watched_page_validates_category(self) -> None:
        """Category must be from valid set."""
        from src.api.scraper import _VALID_CATEGORIES

        assert "delivery" in _VALID_CATEGORIES
        assert "warranty" in _VALID_CATEGORIES
        assert "returns" in _VALID_CATEGORIES
        assert "policies" in _VALID_CATEGORIES
        assert "invalid_category" not in _VALID_CATEGORIES

    @pytest.mark.asyncio
    async def test_add_watched_page_validates_interval(self) -> None:
        """Interval must be between 1 and 8760 — Pydantic rejects invalid values."""
        from pydantic import ValidationError

        from src.api.scraper import WatchedPageCreate

        with pytest.raises(ValidationError, match="rescrape_interval_hours"):
            WatchedPageCreate(url="https://example.com", rescrape_interval_hours=0)

    @pytest.mark.asyncio
    async def test_watched_page_create_model_defaults(self) -> None:
        """Check default values for WatchedPageCreate."""
        from src.api.scraper import WatchedPageCreate

        request = WatchedPageCreate(url="https://example.com")
        assert request.category == "general"
        assert request.rescrape_interval_hours == 168

    @pytest.mark.asyncio
    async def test_watched_page_update_model(self) -> None:
        """WatchedPageUpdate allows partial updates."""
        from src.api.scraper import WatchedPageUpdate

        # Only interval
        update1 = WatchedPageUpdate(rescrape_interval_hours=24)
        assert update1.category is None
        assert update1.rescrape_interval_hours == 24

        # Only category
        update2 = WatchedPageUpdate(category="warranty")
        assert update2.category == "warranty"
        assert update2.rescrape_interval_hours is None


# ─── Rescrape task logic ─────────────────────────────────────


class TestRescrapeWatchedPages:
    """Test the watched pages rescrape pipeline logic."""

    @pytest.mark.asyncio
    async def test_no_pages_due_returns_early(self) -> None:
        """When no pages need rescraping, return early."""
        mock_task = MagicMock()

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))  # no rows

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
            patch("src.tasks.scraper_tasks._get_scraper_config") as mock_config,
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.redis.url = "redis://localhost"
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            mock_redis = AsyncMock()
            mock_redis.aclose = AsyncMock()

            mock_config.return_value = {
                "base_url": "https://prokoleso.ua",
                "request_delay": 1.0,
                "llm_model": "claude-haiku-4-5-20251001",
            }

            with patch("redis.asyncio.Redis.from_url", return_value=mock_redis):
                from src.tasks.scraper_tasks import _rescrape_watched_pages_async

                result = await _rescrape_watched_pages_async(mock_task)

        assert result["status"] == "ok"
        assert result["checked"] == 0

    @pytest.mark.asyncio
    async def test_unchanged_content_skipped(self) -> None:
        """When content hash matches, skip LLM processing."""
        from src.knowledge.scraper import content_hash

        test_content = "Some page content that hasn't changed"
        existing_hash = content_hash(test_content)

        # The page data from DB query
        page_row = MagicMock()
        page_row._mapping = {
            "id": "test-id-123",
            "url": "https://prokoleso.ua/ua/garantiya/",
            "article_id": "article-id-456",
            "content_hash": existing_hash,
            "rescrape_interval_hours": 168,
        }

        # First call returns pages list, subsequent calls return empty (for UPDATE queries)
        mock_result_pages = MagicMock()
        mock_result_pages.__iter__ = MagicMock(return_value=iter([page_row]))

        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result_pages
            return MagicMock()

        mock_conn = AsyncMock()
        mock_conn.execute = mock_execute

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        mock_scraped = MagicMock()
        mock_scraped.title = "Гарантія"
        mock_scraped.content = test_content
        mock_scraped.category = "warranty"

        mock_scraper_instance = AsyncMock()
        mock_scraper_instance.fetch_article = AsyncMock(return_value=mock_scraped)
        mock_scraper_instance.open = AsyncMock()
        mock_scraper_instance.close = AsyncMock()

        mock_task = MagicMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
            patch("src.tasks.scraper_tasks._get_scraper_config") as mock_config,
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.redis.url = "redis://localhost"
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            mock_redis = AsyncMock()
            mock_redis.aclose = AsyncMock()

            mock_config.return_value = {
                "base_url": "https://prokoleso.ua",
                "request_delay": 1.0,
                "llm_model": "claude-haiku-4-5-20251001",
            }

            with (
                patch("redis.asyncio.Redis.from_url", return_value=mock_redis),
                patch("src.knowledge.scraper.ProKolesoScraper", return_value=mock_scraper_instance),
            ):
                from src.tasks.scraper_tasks import _rescrape_watched_pages_async

                result = await _rescrape_watched_pages_async(mock_task)

        assert result["unchanged"] == 1
        assert result["updated"] == 0


# ─── Discovery page tests ──────────────────────────────────


class TestDiscoverPageLinks:
    """Test the discover_page_links method."""

    @pytest.mark.asyncio
    async def test_discover_sub_page_links(self) -> None:
        """discover_page_links should find sub-path links."""
        from src.knowledge.scraper import ProKolesoScraper

        html = """
        <html><body><main>
            <a href="/ua/promotions/promo-1/">Promo 1</a>
            <a href="/ua/promotions/promo-2/">Promo 2</a>
            <a href="/ua/promotions/">Self link</a>
            <a href="/ua/other/">Other section</a>
        </main></body></html>
        """

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=html)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        scraper = ProKolesoScraper(base_url="https://prokoleso.ua")
        scraper._session = mock_session

        links = await scraper.discover_page_links("https://prokoleso.ua/ua/promotions/")
        assert len(links) == 2
        assert "https://prokoleso.ua/ua/promotions/promo-1/" in links
        assert "https://prokoleso.ua/ua/promotions/promo-2/" in links

    @pytest.mark.asyncio
    async def test_discover_empty_page(self) -> None:
        """discover_page_links with no sub-links returns empty list."""
        from src.knowledge.scraper import ProKolesoScraper

        html = "<html><body><main><p>No links here</p></main></body></html>"

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=html)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        scraper = ProKolesoScraper(base_url="https://prokoleso.ua")
        scraper._session = mock_session

        links = await scraper.discover_page_links("https://prokoleso.ua/ua/promotions/")
        assert links == []

    @pytest.mark.asyncio
    async def test_discover_deduplicates(self) -> None:
        """discover_page_links should deduplicate links."""
        from src.knowledge.scraper import ProKolesoScraper

        html = """
        <html><body><main>
            <a href="/ua/promotions/promo-1/">Link 1</a>
            <a href="/ua/promotions/promo-1/">Link 1 again</a>
            <a href="/ua/promotions/promo-1">Without trailing slash</a>
        </main></body></html>
        """

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=html)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        scraper = ProKolesoScraper(base_url="https://prokoleso.ua")
        scraper._session = mock_session

        links = await scraper.discover_page_links("https://prokoleso.ua/ua/promotions/")
        # All three resolve to same abs_url after rstrip("/")
        assert len(links) == 1


class TestDiscoveryModeAPI:
    """Test API model support for discovery mode."""

    def test_watched_page_create_discovery_default(self) -> None:
        from src.api.scraper import WatchedPageCreate

        request = WatchedPageCreate(url="https://example.com")
        assert request.is_discovery is False

    def test_watched_page_create_with_discovery(self) -> None:
        from src.api.scraper import WatchedPageCreate

        request = WatchedPageCreate(url="https://example.com", is_discovery=True)
        assert request.is_discovery is True

    def test_watched_page_update_discovery(self) -> None:
        from src.api.scraper import WatchedPageUpdate

        update = WatchedPageUpdate(is_discovery=True)
        assert update.is_discovery is True

    def test_watched_page_update_no_discovery(self) -> None:
        from src.api.scraper import WatchedPageUpdate

        update = WatchedPageUpdate(category="general")
        assert update.is_discovery is None
