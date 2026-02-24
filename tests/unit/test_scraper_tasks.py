"""Unit tests for scraper tasks — dedup logic and config."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class AsyncContextManagerMock:
    """Helper to mock async context managers (async with)."""

    def __init__(self, return_value: AsyncMock) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> AsyncMock:
        return self._return_value

    async def __aexit__(self, *args: object) -> None:
        pass


def _make_engine_mock(query_result: MagicMock) -> MagicMock:
    """Create a mock engine with begin() returning an async context manager."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=query_result)

    mock_engine = MagicMock()
    mock_engine.begin.return_value = AsyncContextManagerMock(mock_conn)
    return mock_engine


# ─── check_semantic_duplicate (moved to src.knowledge.dedup) ───


class TestCheckDuplicate:
    """Test semantic dedup with mocked embeddings."""

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_duplicate_above_090(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """sim > 0.90 → 'duplicate' (auto-skip)."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_row = MagicMock()
        mock_row.title = "Existing similar article"
        mock_row.similarity = 0.95

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "duplicate"
        assert result["similarity"] == 0.95
        assert result["similar_title"] == "Existing similar article"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_suspect_080_to_090(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """sim 0.80–0.90 → 'suspect'."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_row = MagicMock()
        mock_row.title = "Similar article"
        mock_row.similarity = 0.85

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "suspect"
        assert result["similarity"] == 0.85

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_new_below_080(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """sim < 0.80 → 'new'."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_row = MagicMock()
        mock_row.title = "Different article"
        mock_row.similarity = 0.5

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "new"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_no_embeddings_returns_new(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """When no existing embeddings in DB → 'new'."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_result = MagicMock()
        mock_result.first.return_value = None  # no rows

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "new"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    async def test_no_api_key_returns_new(self, mock_settings: MagicMock) -> None:
        """When no OpenAI API key is set → skip dedup, return 'new'."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = ""
        mock_engine = MagicMock()

        result = await check_semantic_duplicate(mock_engine, "Some content")
        assert result["status"] == "new"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_exception_returns_new(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """On any exception → treat as 'new' (fail-open)."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen_cls.side_effect = Exception("Connection error")
        mock_engine = MagicMock()

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "new"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_boundary_exactly_090_is_suspect(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """sim == 0.90 exactly → 'suspect' (boundary)."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_row = MagicMock()
        mock_row.title = "Boundary article"
        mock_row.similarity = 0.90

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "suspect"

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    async def test_boundary_exactly_080_is_suspect(
        self, mock_gen_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        """sim == 0.80 exactly → 'suspect'."""
        from src.knowledge.dedup import check_semantic_duplicate

        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536
        gen_instance = AsyncMock()
        gen_instance.generate = AsyncMock(return_value=[mock_embedding])
        mock_gen_cls.return_value = gen_instance

        mock_row = MagicMock()
        mock_row.title = "Boundary article"
        mock_row.similarity = 0.80

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        result = await check_semantic_duplicate(mock_engine, "Some content")

        assert result["status"] == "suspect"


# ─── _get_scraper_config ─────────────────────────────────────


class TestGetScraperConfig:
    """Test config loading with new fields."""

    @pytest.mark.asyncio
    async def test_includes_all_fields_from_redis(self) -> None:
        import json

        from src.tasks.scraper_tasks import _get_scraper_config

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "min_date": "2024-01-01",
                    "max_date": "2024-12-31",
                    "dedup_llm_check": True,
                    "schedule_enabled": False,
                    "schedule_hour": 9,
                    "schedule_day_of_week": "friday",
                }
            )
        )

        mock_settings = MagicMock()
        mock_settings.scraper.enabled = False
        mock_settings.scraper.base_url = "https://prokoleso.ua"
        mock_settings.scraper.info_path = "/ua/info/"
        mock_settings.scraper.max_pages = 3
        mock_settings.scraper.request_delay = 2.0
        mock_settings.scraper.auto_approve = False
        mock_settings.scraper.llm_model = "claude-haiku-4-5-20251001"
        mock_settings.scraper.schedule_enabled = True
        mock_settings.scraper.schedule_hour = 6
        mock_settings.scraper.schedule_day_of_week = "monday"
        mock_settings.scraper.min_date = ""
        mock_settings.scraper.max_date = ""
        mock_settings.scraper.dedup_llm_check = False

        config = await _get_scraper_config(mock_redis, mock_settings)

        assert config["min_date"] == "2024-01-01"
        assert config["max_date"] == "2024-12-31"
        assert config["dedup_llm_check"] is True
        assert config["schedule_enabled"] is False
        assert config["schedule_hour"] == 9
        assert config["schedule_day_of_week"] == "friday"

    @pytest.mark.asyncio
    async def test_falls_back_to_env_defaults(self) -> None:
        from src.tasks.scraper_tasks import _get_scraper_config

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.scraper.enabled = False
        mock_settings.scraper.base_url = "https://prokoleso.ua"
        mock_settings.scraper.info_path = "/ua/info/"
        mock_settings.scraper.max_pages = 3
        mock_settings.scraper.request_delay = 2.0
        mock_settings.scraper.auto_approve = False
        mock_settings.scraper.llm_model = "claude-haiku-4-5-20251001"
        mock_settings.scraper.schedule_enabled = True
        mock_settings.scraper.schedule_hour = 6
        mock_settings.scraper.schedule_day_of_week = "monday"
        mock_settings.scraper.min_date = ""
        mock_settings.scraper.max_date = ""
        mock_settings.scraper.dedup_llm_check = False

        config = await _get_scraper_config(mock_redis, mock_settings)

        assert config["min_date"] == ""
        assert config["max_date"] == ""
        assert config["dedup_llm_check"] is False
        assert config["schedule_enabled"] is True
        assert config["schedule_hour"] == 6
        assert config["schedule_day_of_week"] == "monday"
