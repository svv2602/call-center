"""Unit tests for multi-source Celery tasks — run_all_sources, run_source."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class AsyncContextManagerMock:
    """Helper to mock async context managers."""

    def __init__(self, return_value: AsyncMock) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> AsyncMock:
        return self._return_value

    async def __aexit__(self, *args: object) -> None:
        pass


class TestRunAllSources:
    """Test run_all_sources dispatching logic."""

    @pytest.mark.asyncio
    async def test_no_enabled_sources(self) -> None:
        """When no sources are enabled, dispatch nothing."""
        mock_task = MagicMock()

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            from src.tasks.scraper_tasks import _run_all_sources_async

            result = await _run_all_sources_async(mock_task)

        assert result["status"] == "ok"
        assert result["dispatched"] == 0

    @pytest.mark.asyncio
    async def test_dispatches_enabled_sources(self) -> None:
        """Enabled sources should be dispatched."""
        mock_task = MagicMock()

        # Two enabled source configs
        row1 = MagicMock()
        row1._mapping = {
            "id": "config-1",
            "name": "Source 1",
            "schedule_enabled": True,
            "schedule_hour": 6,
            "schedule_day_of_week": "monday",
        }
        row2 = MagicMock()
        row2._mapping = {
            "id": "config-2",
            "name": "Source 2",
            "schedule_enabled": True,
            "schedule_hour": 6,
            "schedule_day_of_week": "monday",
        }

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([row1, row2]))

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
            patch("src.tasks.scraper_tasks.run_source") as mock_run_source,
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            mock_run_source.delay = MagicMock()

            from src.tasks.scraper_tasks import _run_all_sources_async

            result = await _run_all_sources_async(mock_task, triggered_by="manual")

        assert result["dispatched"] == 2
        assert mock_run_source.delay.call_count == 2


class TestRunSource:
    """Test run_source pipeline."""

    @pytest.mark.asyncio
    async def test_config_not_found(self) -> None:
        """Unknown config ID returns error."""
        mock_task = MagicMock()

        mock_result = MagicMock()
        mock_result.first.return_value = None

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            from src.tasks.scraper_tasks import _run_source_async

            result = await _run_source_async(mock_task, "nonexistent-id")

        assert result["status"] == "error"
        assert result["reason"] == "config_not_found"

    @pytest.mark.asyncio
    async def test_disabled_source_skipped(self) -> None:
        """Disabled source should be skipped."""
        mock_task = MagicMock()

        mock_row = MagicMock()
        mock_row._mapping = {
            "id": "config-1",
            "name": "Test Source",
            "source_type": "rss",
            "source_url": "https://example.com/rss.xml",
            "language": "de",
            "enabled": False,
            "auto_approve": False,
            "request_delay": 2.0,
            "max_articles_per_run": 20,
            "settings": "{}",
        }

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            from src.tasks.scraper_tasks import _run_source_async

            result = await _run_source_async(mock_task, "config-1")

        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_no_discovered_articles(self) -> None:
        """When fetcher discovers no articles, return ok with zero counts."""
        mock_task = MagicMock()

        mock_row = MagicMock()
        mock_row._mapping = {
            "id": "config-1",
            "name": "Test Source",
            "source_type": "rss",
            "source_url": "https://example.com/rss.xml",
            "language": "de",
            "enabled": True,
            "auto_approve": False,
            "request_delay": 0.0,
            "max_articles_per_run": 20,
            "settings": "{}",
        }

        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: load config
                result = MagicMock()
                result.first.return_value = mock_row
                return result
            # Subsequent calls: update run stats
            return MagicMock()

        mock_conn = AsyncMock()
        mock_conn.execute = mock_execute

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
        mock_engine.dispose = AsyncMock()

        mock_fetcher = AsyncMock()
        mock_fetcher.discover_articles = AsyncMock(return_value=[])
        mock_fetcher.open = AsyncMock()
        mock_fetcher.close = AsyncMock()

        with (
            patch("src.tasks.scraper_tasks.get_settings") as mock_settings,
            patch("src.tasks.scraper_tasks.create_async_engine", return_value=mock_engine),
            patch("src.knowledge.fetchers.create_fetcher", return_value=mock_fetcher),
        ):
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.database.url = "postgresql+asyncpg://localhost/test"

            from src.tasks.scraper_tasks import _run_source_async

            result = await _run_source_async(mock_task, "config-1")

        assert result["status"] == "ok"
        assert result["discovered"] == 0
        assert result["processed"] == 0


class TestSourceConfigUpdate:
    """Test _update_source_config_run helper."""

    @pytest.mark.asyncio
    async def test_updates_run_status(self) -> None:
        """Should update last_run_* fields."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)

        from src.tasks.scraper_tasks import _update_source_config_run

        await _update_source_config_run(
            mock_engine, "config-id", "ok", {"processed": 5, "skipped": 2, "errors": 0}
        )

        mock_conn.execute.assert_called_once()
