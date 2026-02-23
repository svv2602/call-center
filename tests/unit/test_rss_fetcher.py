"""Unit tests for RSS fetcher — parsing, title filtering, date extraction."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
    <title>Auto Bild</title>
    <item>
        <title>Winterreifen-Test 2025: Die besten Reifen</title>
        <link>https://www.autobild.de/artikel/winterreifen-test-2025-123.html</link>
        <pubDate>Mon, 10 Feb 2025 10:00:00 +0100</pubDate>
    </item>
    <item>
        <title>Neuer BMW M3 im Test</title>
        <link>https://www.autobild.de/artikel/bmw-m3-test-456.html</link>
        <pubDate>Sun, 09 Feb 2025 08:00:00 +0100</pubDate>
    </item>
    <item>
        <title>Sommerreifen im Vergleich: Tire test results</title>
        <link>https://www.autobild.de/artikel/sommerreifen-test-789.html</link>
        <pubDate>Sat, 08 Feb 2025 12:00:00 +0100</pubDate>
    </item>
</channel>
</rss>"""


class TestRSSDateParsing:
    """Test RSS date parsing utility."""

    def test_rfc2822_date(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import _parse_rss_date

        result = _parse_rss_date("Mon, 10 Feb 2025 10:00:00 +0100")
        assert result == "2025-02-10"

    def test_iso_date(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import _parse_rss_date

        result = _parse_rss_date("2025-02-10T10:00:00")
        assert result == "2025-02-10"

    def test_iso_date_only(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import _parse_rss_date

        result = _parse_rss_date("2025-02-10")
        assert result == "2025-02-10"

    def test_invalid_date(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import _parse_rss_date

        result = _parse_rss_date("not a date")
        assert result == ""


class TestRSSFetcherInit:
    """Test RSSFetcher initialization."""

    def test_creates_with_filter(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(
            feed_url="https://example.com/rss.xml",
            title_filter_regex="(?i)reifen|tire",
        )
        assert fetcher._title_filter is not None
        assert fetcher._title_filter.search("Winterreifen-Test")
        assert not fetcher._title_filter.search("BMW M3 im Test")

    def test_creates_without_filter(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml")
        assert fetcher._title_filter is None


class TestRSSFetcherDiscovery:
    """Test RSS feed discovery with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_discover_with_title_filter(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(
            feed_url="https://www.autobild.de/rss/test.xml",
            title_filter_regex="(?i)reifen|tire",
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_RSS)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        articles = await fetcher.discover_articles(max_articles=20)

        # Should match "Winterreifen-Test" and "Sommerreifen...Tire test"
        # but NOT "BMW M3 im Test"
        assert len(articles) == 2
        assert "winterreifen" in articles[0]["url"].lower()
        assert articles[0]["published"] == "2025-02-10"
        assert "sommerreifen" in articles[1]["url"].lower()

    @pytest.mark.asyncio
    async def test_discover_without_filter(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_RSS)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        articles = await fetcher.discover_articles(max_articles=20)
        assert len(articles) == 3  # all items

    @pytest.mark.asyncio
    async def test_discover_respects_max_articles(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_RSS)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        articles = await fetcher.discover_articles(max_articles=1)
        assert len(articles) == 1

    @pytest.mark.asyncio
    async def test_discover_min_date_filter(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_RSS)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        # Only articles from Feb 10 and later
        articles = await fetcher.discover_articles(min_date=datetime.date(2025, 2, 10))
        assert len(articles) == 1
        assert "winterreifen" in articles[0]["url"].lower()

    @pytest.mark.asyncio
    async def test_discover_http_error(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml")

        mock_resp = AsyncMock()
        mock_resp.status = 404

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        articles = await fetcher.discover_articles()
        assert articles == []


class TestRSSFetcherFetchArticle:
    """Test RSS article fetching with trafilatura."""

    @pytest.mark.asyncio
    async def test_fetch_article_success(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml", request_delay=0)

        html = """
        <html><head><title>Winterreifen-Test 2025</title></head>
        <body><article>
        <h1>Winterreifen-Test 2025</h1>
        <p>Der große Winterreifentest mit 50 verschiedenen Modellen.
        Wir haben alle getestet und die Ergebnisse zusammengefasst.
        Hier finden Sie detaillierte Testergebnisse und Empfehlungen für alle Fahrzeugklassen.</p>
        </article></body></html>
        """

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        with patch("src.knowledge.fetchers.rss_fetcher.trafilatura") as mock_traf:
            mock_traf.extract.return_value = (
                "Der große Winterreifentest mit 50 verschiedenen Modellen. " * 10
            )
            mock_metadata = MagicMock()
            mock_metadata.title = "Winterreifen-Test 2025"
            mock_traf.extract_metadata.return_value = mock_metadata

            result = await fetcher.fetch_article("https://example.com/test", published="2025-02-10")

        assert result is not None
        assert result.title == "Winterreifen-Test 2025"
        assert result.published == "2025-02-10"
        assert result.category == "comparisons"

    @pytest.mark.asyncio
    async def test_fetch_article_too_short(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml", request_delay=0)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="<html><body>Short</body></html>")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        with patch("src.knowledge.fetchers.rss_fetcher.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Short"

            result = await fetcher.fetch_article("https://example.com/test")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_article_http_error(self) -> None:
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = RSSFetcher(feed_url="https://example.com/rss.xml", request_delay=0)

        mock_resp = AsyncMock()
        mock_resp.status = 500

        mock_session = AsyncMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(),
            )
        )
        fetcher._session = mock_session

        result = await fetcher.fetch_article("https://example.com/test")
        assert result is None
