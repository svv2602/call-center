"""Unit tests for generic HTML fetcher — link discovery, content extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SAMPLE_LISTING_HTML = """
<html><body>
<div class="article-list">
    <a href="/rund-ums-fahrzeug/tests/reifentest/winterreifen-2025/">
        <h3>Winterreifen-Test 2025: 50 Reifen im Check</h3>
    </a>
    <a href="/rund-ums-fahrzeug/tests/reifentest/sommerreifen-2024/">
        <h3>Sommerreifen-Test 2024</h3>
    </a>
    <a href="https://other-domain.com/article">External link</a>
    <a href="/login">Login</a>
    <a href="#anchor">Anchor</a>
    <a href="/rund-ums-fahrzeug/tests/reifentest/">Same as listing</a>
    <a href="/rund-ums-fahrzeug/tests/reifentest/ganzjahresreifen/">
        <h3>Ganzjahresreifen im Test</h3>
    </a>
</div>
</body></html>
"""


class TestGenericHTMLFetcherInit:
    """Test GenericHTMLFetcher initialization."""

    def test_base_domain_extraction(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            listing_urls=["https://www.adac.de/tests/reifentest/"],
        )
        assert fetcher._base_domain == "www.adac.de"

    def test_base_url_trailing_slash_stripped(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de/",
            listing_urls=[],
        )
        assert fetcher._base_url == "https://www.adac.de"


class TestGenericHTMLLinkExtraction:
    """Test link extraction from listing HTML."""

    def test_extract_article_links(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            listing_urls=["https://www.adac.de/rund-ums-fahrzeug/tests/reifentest/"],
        )

        links = fetcher._extract_links(
            SAMPLE_LISTING_HTML,
            "https://www.adac.de/rund-ums-fahrzeug/tests/reifentest/",
        )

        urls = [item["url"] for item in links]

        # Should include the 3 article links on same domain, deeper than listing
        assert len(links) == 3
        assert any("winterreifen-2025" in u for u in urls)
        assert any("sommerreifen-2024" in u for u in urls)
        assert any("ganzjahresreifen" in u for u in urls)

        # Should NOT include: external domain, login, anchor, listing itself
        assert not any("other-domain.com" in u for u in urls)
        assert not any("/login" in u for u in urls)

    def test_extract_links_filters_non_article_urls(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            listing_urls=[],
        )

        html = """
        <html><body>
            <a href="/tests/reifentest/checkout"><h3>Checkout Page</h3></a>
            <a href="/tests/reifentest/article.pdf"><h3>PDF Download</h3></a>
            <a href="/tests/reifentest/search"><h3>Search Page</h3></a>
            <a href="/tests/reifentest/actual-article/"><h3>Real Article Title Here</h3></a>
        </body></html>
        """

        links = fetcher._extract_links(html, "https://www.adac.de/tests/reifentest/")
        urls = [item["url"] for item in links]

        assert len(links) == 1
        assert "actual-article" in urls[0]


class TestGenericHTMLSameSite:
    """Test domain matching."""

    def test_same_domain(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(base_url="https://www.adac.de", listing_urls=[])
        assert fetcher._is_same_site("www.adac.de")

    def test_subdomain(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(base_url="https://www.adac.de", listing_urls=[])
        assert fetcher._is_same_site("press.adac.de") is False  # subdomain of adac.de, not www.adac.de

    def test_different_domain(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(base_url="https://www.adac.de", listing_urls=[])
        assert fetcher._is_same_site("www.other.de") is False


class TestGenericHTMLDiscovery:
    """Test article discovery with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_discover_articles(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            request_delay=0,
            listing_urls=["https://www.adac.de/rund-ums-fahrzeug/tests/reifentest/"],
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_LISTING_HTML)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(),
        ))
        fetcher._session = mock_session

        articles = await fetcher.discover_articles(max_articles=20)
        assert len(articles) == 3

    @pytest.mark.asyncio
    async def test_discover_respects_max(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            request_delay=0,
            listing_urls=["https://www.adac.de/rund-ums-fahrzeug/tests/reifentest/"],
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_LISTING_HTML)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(),
        ))
        fetcher._session = mock_session

        articles = await fetcher.discover_articles(max_articles=1)
        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_discover_http_error(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            request_delay=0,
            listing_urls=["https://www.adac.de/tests/"],
        )

        mock_resp = AsyncMock()
        mock_resp.status = 403

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(),
        ))
        fetcher._session = mock_session

        articles = await fetcher.discover_articles()
        assert articles == []


class TestGenericHTMLFetchArticle:
    """Test article fetching with trafilatura."""

    @pytest.mark.asyncio
    async def test_fetch_article_success(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            request_delay=0,
            listing_urls=[],
        )

        html = "<html><head><title>Test Article</title></head><body><p>Content</p></body></html>"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(),
        ))
        fetcher._session = mock_session

        with patch("src.knowledge.fetchers.generic_html.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Extracted content " * 20
            mock_metadata = MagicMock()
            mock_metadata.title = "Test Article"
            mock_traf.extract_metadata.return_value = mock_metadata

            result = await fetcher.fetch_article(
                "https://www.adac.de/test/article/", published="2025-01-15"
            )

        assert result is not None
        assert result.title == "Test Article"
        assert result.published == "2025-01-15"
        assert result.category == "comparisons"

    @pytest.mark.asyncio
    async def test_fetch_article_short_content_returns_none(self) -> None:
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = GenericHTMLFetcher(
            base_url="https://www.adac.de",
            request_delay=0,
            listing_urls=[],
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="<html><body>Short</body></html>")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(),
        ))
        fetcher._session = mock_session

        with patch("src.knowledge.fetchers.generic_html.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Short"

            result = await fetcher.fetch_article("https://www.adac.de/test/")

        assert result is None
