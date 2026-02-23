"""Unit tests for fetcher factory — create_fetcher dispatch."""

from __future__ import annotations

import pytest


class TestCreateFetcher:
    """Test create_fetcher dispatches to correct implementation."""

    def test_prokoleso_type(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher
        from src.knowledge.fetchers.prokoleso import ProKolesoFetcher

        fetcher = create_fetcher(
            {
                "source_type": "prokoleso",
                "source_url": "https://prokoleso.ua",
                "request_delay": 1.0,
                "settings": {"info_path": "/ua/info/", "max_pages": 3},
            }
        )
        assert isinstance(fetcher, ProKolesoFetcher)

    def test_rss_type(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = create_fetcher(
            {
                "source_type": "rss",
                "source_url": "https://example.com/rss.xml",
                "request_delay": 2.0,
                "settings": {"title_filter_regex": "(?i)tire"},
            }
        )
        assert isinstance(fetcher, RSSFetcher)

    def test_generic_html_type(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher
        from src.knowledge.fetchers.generic_html import GenericHTMLFetcher

        fetcher = create_fetcher(
            {
                "source_type": "generic_html",
                "source_url": "https://www.adac.de",
                "request_delay": 2.0,
                "settings": {"listing_urls": ["https://www.adac.de/tests/"]},
            }
        )
        assert isinstance(fetcher, GenericHTMLFetcher)

    def test_unknown_type_raises(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher

        with pytest.raises(ValueError, match="Unknown source_type"):
            create_fetcher(
                {
                    "source_type": "unknown",
                    "source_url": "https://example.com",
                }
            )

    def test_prokoleso_default_settings(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher
        from src.knowledge.fetchers.prokoleso import ProKolesoFetcher

        fetcher = create_fetcher(
            {
                "source_type": "prokoleso",
                "source_url": "https://prokoleso.ua",
                "settings": {},
            }
        )
        assert isinstance(fetcher, ProKolesoFetcher)

    def test_rss_no_filter(self) -> None:
        from src.knowledge.fetchers.base import create_fetcher
        from src.knowledge.fetchers.rss_fetcher import RSSFetcher

        fetcher = create_fetcher(
            {
                "source_type": "rss",
                "source_url": "https://example.com/feed",
                "settings": {},
            }
        )
        assert isinstance(fetcher, RSSFetcher)
        assert fetcher._title_filter is None
