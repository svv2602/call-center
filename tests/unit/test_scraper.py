"""Unit tests for ProKoleso HTML scraper."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.knowledge.scraper import ProKolesoScraper, ScrapedArticle, _parse_date_text


class AsyncContextManagerMock:
    """Helper to mock async context managers (async with)."""

    def __init__(self, return_value: AsyncMock) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> AsyncMock:
        return self._return_value

    async def __aexit__(self, *args: object) -> None:
        pass


# ─── Category extraction ────────────────────────────────────


class TestExtractCategory:
    """Test URL slug → KB category mapping."""

    @pytest.fixture
    def scraper(self) -> ProKolesoScraper:
        return ProKolesoScraper()

    def test_novosti_maps_to_general(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/novosti/tire-news/") == "general"

    def test_testy_maps_to_comparisons(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/testy-i-obzory-shin/best-2024/") == "comparisons"

    def test_vse_o_shinah_maps_to_guides(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/vse-o-shinah/tire-pressure/") == "guides"

    def test_vse_o_diskah_maps_to_guides(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/vse-o-diskah/pcd-explained/") == "guides"

    def test_pokupatelu_maps_to_faq(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/pokupatelu/how-to-buy/") == "faq"

    def test_unknown_slug_defaults_to_general(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/random-unknown-slug/article/") == "general"

    def test_case_insensitive(self, scraper: ProKolesoScraper) -> None:
        assert scraper._extract_category("https://prokoleso.ua/ua/info/NOVOSTI/something/") == "general"


# ─── HTML parsing ────────────────────────────────────────────


class TestParseArticleContent:
    """Test HTML → markdown content extraction."""

    @pytest.fixture
    def scraper(self) -> ProKolesoScraper:
        return ProKolesoScraper()

    def test_extracts_h1_title(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><h1>Як обрати зимові шини</h1><article><p>Content here</p></article></body></html>"
        title, _ = scraper._parse_article_content(html)
        assert title == "Як обрати зимові шини"

    def test_missing_h1_returns_default_title(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><article><p>Content without heading</p></article></body></html>"
        title, _ = scraper._parse_article_content(html)
        assert title == "Без названия"

    def test_extracts_paragraph_text(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><article><p>Зимові шини потрібні при температурі нижче +7°C.</p></article></body></html>"
        _, content = scraper._parse_article_content(html)
        assert "Зимові шини потрібні при температурі нижче +7°C." in content

    def test_extracts_headings_as_markdown(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><article><h2>Переваги</h2><p>Text</p><h3>Деталі</h3><p>More</p></article></body></html>"
        _, content = scraper._parse_article_content(html)
        assert "## Переваги" in content
        assert "### Деталі" in content

    def test_extracts_list_items(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><article><ul><li>Michelin</li><li>Continental</li></ul></article></body></html>"
        _, content = scraper._parse_article_content(html)
        assert "- Michelin" in content
        assert "- Continental" in content

    def test_extracts_table_rows(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body><article>
            <table><tr><th>Бренд</th><th>Ціна</th></tr>
            <tr><td>Michelin</td><td>3500</td></tr></table>
        </article></body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "| Бренд | Ціна |" in content
        assert "| Michelin | 3500 |" in content

    def test_removes_scripts_and_styles(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body><article>
            <script>alert('xss')</script>
            <style>.hidden{display:none}</style>
            <p>Clean content</p>
        </article></body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "alert" not in content
        assert ".hidden" not in content
        assert "Clean content" in content

    def test_removes_promotional_elements(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body><article>
            <p>Useful info about tires</p>
            <div class="promo-banner">Buy now!</div>
            <div class="social-share">Share this</div>
        </article></body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Useful info" in content
        assert "Buy now" not in content
        assert "Share this" not in content

    def test_removes_product_cards(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body><article>
            <p>Useful content</p>
            <div class="product-card"><span class="price">1999 грн</span></div>
        </article></body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Useful content" in content
        assert "1999" not in content

    def test_finds_article_content_by_id(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body>
            <nav>Navigation</nav>
            <div id="article-content"><p>Main article text here</p></div>
            <footer>Footer</footer>
        </body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Main article text" in content

    def test_finds_article_tag(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body>
            <nav>Nav</nav>
            <article><p>Article body content</p></article>
        </body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Article body content" in content

    def test_finds_content_by_class(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body>
            <div class="article-content"><p>Found by class</p></div>
        </body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Found by class" in content

    def test_fallback_to_body(self, scraper: ProKolesoScraper) -> None:
        html = "<html><body><p>Fallback content in body</p></body></html>"
        _, content = scraper._parse_article_content(html)
        assert "Fallback content" in content

    def test_empty_body_returns_empty(self, scraper: ProKolesoScraper) -> None:
        html = "<html><head><title>T</title></head></html>"
        _, content = scraper._parse_article_content(html)
        assert content == ""

    def test_removes_nav_footer_from_output(self, scraper: ProKolesoScraper) -> None:
        html = """<html><body>
            <nav>Menu items</nav>
            <article><p>Article text</p></article>
            <footer>Copyright</footer>
        </body></html>"""
        _, content = scraper._parse_article_content(html)
        assert "Menu items" not in content
        assert "Copyright" not in content


# ─── Link extraction ─────────────────────────────────────────


class TestExtractArticleLinks:
    """Test article link extraction from listing HTML."""

    @pytest.fixture
    def scraper(self) -> ProKolesoScraper:
        return ProKolesoScraper()

    def test_extracts_article_links(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = """<div>
            <a href="/ua/info/vse-o-shinah/tire-guide/"><h2>Гайд по шинах</h2></a>
            <a href="/ua/info/novosti/news-item/"><h3>Новина про шини</h3></a>
        </div>"""
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 2
        assert results[0]["title"] == "Гайд по шинах"
        assert results[0]["url"].endswith("/ua/info/vse-o-shinah/tire-guide/")
        assert "published" in results[0]  # published field always present

    def test_skips_listing_page_link(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="/ua/info/">Усі статті</a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 0

    def test_skips_pagination_links(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="/ua/info/page/2/">Сторінка 2</a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 0

    def test_skips_short_titles(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="/ua/info/some-article/">OK</a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 0

    def test_handles_absolute_urls(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="https://prokoleso.ua/ua/info/article-slug/"><h2>Абсолютне посилання</h2></a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 1
        assert results[0]["url"] == "https://prokoleso.ua/ua/info/article-slug/"

    def test_resolves_relative_urls(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="/ua/info/relative-article/"><h2>Відносне посилання</h2></a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 1
        assert results[0]["url"] == "https://prokoleso.ua/ua/info/relative-article/"

    def test_deduplication_not_in_extract(self, scraper: ProKolesoScraper) -> None:
        """_extract_article_links does NOT deduplicate; that's discover_article_urls' job."""
        from bs4 import BeautifulSoup

        html = """<div>
            <a href="/ua/info/article/"><h2>Same article</h2></a>
            <a href="/ua/info/article/"><h3>Same article again</h3></a>
        </div>"""
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 2

    def test_uses_link_text_when_no_heading(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = '<div><a href="/ua/info/article-slug/">Текст посилання без заголовку</a></div>'
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 1
        assert results[0]["title"] == "Текст посилання без заголовку"


# ─── discover_article_urls ───────────────────────────────────


class TestDiscoverArticleUrls:
    """Test full discovery flow with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_discovers_from_single_page(self) -> None:
        html = """<div>
            <a href="/ua/info/article-1/"><h2>Стаття перша — тест шин</h2></a>
            <a href="/ua/info/article-2/"><h2>Стаття друга — огляд</h2></a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(max_pages=1)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_stops_on_non_200(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.text = AsyncMock(return_value="Not Found")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(max_pages=3)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self) -> None:
        html = "<div>No articles here</div>"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(max_pages=3)
        assert len(results) == 0
        # Should have only made 1 request (stopped after empty page)
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_deduplicates_across_pages(self) -> None:
        html = """<div>
            <a href="/ua/info/same-article/"><h2>Однакова стаття</h2></a>
        </div>"""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(max_pages=2)
        # Same URL on both pages → only 1 result
        assert len(results) == 1


# ─── fetch_article ───────────────────────────────────────────


class TestFetchArticle:
    """Test article fetching with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_fetches_and_parses_article(self) -> None:
        html = """<html><body>
            <h1>Тест зимових шин 2024</h1>
            <article>
                <p>Ми протестували 10 моделей зимових шин на мокрій та сухій дорозі.</p>
                <p>Результати показали значну різницю у гальмівному шляху між преміум та бюджетними шинами.</p>
            </article>
        </body></html>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper()
        scraper._session = mock_session

        result = await scraper.fetch_article("https://prokoleso.ua/ua/info/testy-i-obzory-shin/test-2024/")
        assert result is not None
        assert isinstance(result, ScrapedArticle)
        assert result.title == "Тест зимових шин 2024"
        assert result.category == "comparisons"
        assert "протестували" in result.content

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.text = AsyncMock(return_value="Not Found")

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper()
        scraper._session = mock_session

        result = await scraper.fetch_article("https://prokoleso.ua/ua/info/missing/")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_short_content(self) -> None:
        html = "<html><body><h1>Title</h1><article><p>Short</p></article></body></html>"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper()
        scraper._session = mock_session

        result = await scraper.fetch_article("https://prokoleso.ua/ua/info/short-article/")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=Exception("Connection refused"))

        scraper = ProKolesoScraper()
        scraper._session = mock_session

        result = await scraper.fetch_article("https://prokoleso.ua/ua/info/unreachable/")
        assert result is None


# ─── Date parsing ─────────────────────────────────────────────


class TestParseDateText:
    """Test date text parsing utility."""

    def test_dd_mm_yyyy_format(self) -> None:
        assert _parse_date_text("20.09.2024") == "2024-09-20"

    def test_single_digit_day_month(self) -> None:
        assert _parse_date_text("1.2.2024") == "2024-02-01"

    def test_iso_format_passthrough(self) -> None:
        assert _parse_date_text("2024-09-20") == "2024-09-20"

    def test_invalid_date_returns_empty(self) -> None:
        assert _parse_date_text("32.13.2024") == ""

    def test_empty_string(self) -> None:
        assert _parse_date_text("") == ""

    def test_garbage_text(self) -> None:
        assert _parse_date_text("no date here") == ""

    def test_whitespace_stripped(self) -> None:
        assert _parse_date_text("  10.02.2026  ") == "2026-02-10"


class TestExtractArticleLinksWithDates:
    """Test date extraction from article cards."""

    @pytest.fixture
    def scraper(self) -> ProKolesoScraper:
        return ProKolesoScraper()

    def test_extracts_date_from_article_card(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = """<div>
            <a href="/ua/info/article-1/" class="article-card">
                <div class="article-date date">10.02.2026</div>
                <div class="article-title"><h2>Стаття з датою</h2></div>
            </a>
        </div>"""
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 1
        assert results[0]["published"] == "2026-02-10"

    def test_no_date_returns_empty(self, scraper: ProKolesoScraper) -> None:
        from bs4 import BeautifulSoup

        html = """<div>
            <a href="/ua/info/article-1/">
                <h2>Стаття без дати</h2>
            </a>
        </div>"""
        soup = BeautifulSoup(html, "lxml")
        results = scraper._extract_article_links(soup)
        assert len(results) == 1
        assert results[0]["published"] == ""


# ─── date range filtering ─────────────────────────────────────


class TestDiscoverWithMinDate:
    """Test date-based filtering in discover_article_urls."""

    @pytest.mark.asyncio
    async def test_filters_old_articles(self) -> None:
        html = """<div>
            <a href="/ua/info/new-article/" class="article-card">
                <div class="article-date">10.02.2026</div>
                <h2>Нова стаття тест шин</h2>
            </a>
            <a href="/ua/info/old-article/" class="article-card">
                <div class="article-date">15.06.2023</div>
                <h2>Стара стаття шини</h2>
            </a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(
            max_pages=1, min_date=datetime.date(2025, 1, 1)
        )
        assert len(results) == 1
        assert "new-article" in results[0]["url"]

    @pytest.mark.asyncio
    async def test_stops_when_all_too_old(self) -> None:
        html = """<div>
            <a href="/ua/info/old-1/" class="article-card">
                <div class="article-date">15.06.2020</div>
                <h2>Старша стаття номер один</h2>
            </a>
            <a href="/ua/info/old-2/" class="article-card">
                <div class="article-date">10.03.2020</div>
                <h2>Старша стаття номер два</h2>
            </a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(
            max_pages=5, min_date=datetime.date(2024, 1, 1)
        )
        assert len(results) == 0
        # Should stop after page 1 since all articles are too old
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_no_min_date_returns_all(self) -> None:
        html = """<div>
            <a href="/ua/info/article-1/" class="article-card">
                <div class="article-date">15.06.2020</div>
                <h2>Стаття без фільтру дати</h2>
            </a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(max_pages=1, min_date=None)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_max_date_filters_newer_articles(self) -> None:
        html = """<div>
            <a href="/ua/info/new-article/" class="article-card">
                <div class="article-date">10.02.2026</div>
                <h2>Нова стаття — тестування</h2>
            </a>
            <a href="/ua/info/old-article/" class="article-card">
                <div class="article-date">15.06.2024</div>
                <h2>Стаття з 2024 року тест</h2>
            </a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(
            max_pages=1, max_date=datetime.date(2024, 12, 31)
        )
        assert len(results) == 1
        assert "old-article" in results[0]["url"]

    @pytest.mark.asyncio
    async def test_date_range_filters_both_ends(self) -> None:
        html = """<div>
            <a href="/ua/info/too-new/" class="article-card">
                <div class="article-date">10.02.2026</div>
                <h2>Занадто нова стаття тест</h2>
            </a>
            <a href="/ua/info/in-range/" class="article-card">
                <div class="article-date">15.06.2024</div>
                <h2>Стаття в діапазоні тест</h2>
            </a>
            <a href="/ua/info/too-old/" class="article-card">
                <div class="article-date">01.01.2023</div>
                <h2>Занадто стара стаття тест</h2>
            </a>
        </div>"""

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        results = await scraper.discover_article_urls(
            max_pages=1,
            min_date=datetime.date(2024, 1, 1),
            max_date=datetime.date(2024, 12, 31),
        )
        assert len(results) == 1
        assert "in-range" in results[0]["url"]


# ─── Pagination URL fix ──────────────────────────────────────


class TestPaginationUrl:
    """Test that pagination uses ?page=N format."""

    @pytest.mark.asyncio
    async def test_page_2_uses_query_param(self) -> None:
        """Page 2+ should use ?page=N, not /page/N/."""
        html_with_articles = """<div>
            <a href="/ua/info/article-a/"><h2>Стаття A — тестування шин</h2></a>
        </div>"""
        html_empty = "<div>No articles here</div>"

        call_count = 0
        captured_urls: list[str] = []

        def mock_get(url: str, **kwargs: object) -> AsyncContextManagerMock:
            nonlocal call_count
            call_count += 1
            captured_urls.append(url)
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.text = AsyncMock(
                return_value=html_with_articles if call_count <= 2 else html_empty
            )
            return AsyncContextManagerMock(mock_resp)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=mock_get)

        scraper = ProKolesoScraper(request_delay=0)
        scraper._session = mock_session

        await scraper.discover_article_urls(max_pages=3)

        # Page 1: no query param; Page 2+: ?page=N
        assert "?page=" not in captured_urls[0]
        assert captured_urls[1].endswith("?page=2")
        assert captured_urls[2].endswith("?page=3")
        # Must NOT use /page/N/ format
        for url in captured_urls:
            assert "/page/" not in url


# ─── Open/close lifecycle ────────────────────────────────────


class TestScraperLifecycle:
    """Test open/close session management."""

    @pytest.mark.asyncio
    async def test_open_creates_session(self) -> None:
        scraper = ProKolesoScraper()
        assert scraper._session is None
        await scraper.open()
        assert scraper._session is not None
        await scraper.close()

    @pytest.mark.asyncio
    async def test_close_nullifies_session(self) -> None:
        scraper = ProKolesoScraper()
        await scraper.open()
        await scraper.close()
        assert scraper._session is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        scraper = ProKolesoScraper()
        await scraper.close()  # no-op when not opened
        assert scraper._session is None

    @pytest.mark.asyncio
    async def test_discover_without_open_raises(self) -> None:
        scraper = ProKolesoScraper()
        with pytest.raises(AssertionError, match="Call open"):
            await scraper.discover_article_urls()

    @pytest.mark.asyncio
    async def test_fetch_without_open_raises(self) -> None:
        scraper = ProKolesoScraper()
        with pytest.raises(AssertionError, match="Call open"):
            await scraper.fetch_article("https://example.com")
