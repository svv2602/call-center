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


# ─── _check_duplicate ────────────────────────────────────────


class TestCheckDuplicate:
    """Test semantic dedup with mocked embeddings."""

    @pytest.mark.asyncio
    async def test_duplicate_above_090(self) -> None:
        """sim > 0.90 → 'duplicate' (auto-skip)."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_row = MagicMock()
        mock_row.title = "Existing similar article"
        mock_row.similarity = 0.95

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "duplicate"
        assert result["similarity"] == 0.95
        assert result["similar_title"] == "Existing similar article"

    @pytest.mark.asyncio
    async def test_suspect_080_to_090(self) -> None:
        """sim 0.80–0.90 → 'suspect'."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_row = MagicMock()
        mock_row.title = "Similar article"
        mock_row.similarity = 0.85

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "suspect"
        assert result["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_new_below_080(self) -> None:
        """sim < 0.80 → 'new'."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_row = MagicMock()
        mock_row.title = "Different article"
        mock_row.similarity = 0.5

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "new"

    @pytest.mark.asyncio
    async def test_no_embeddings_returns_new(self) -> None:
        """When no existing embeddings in DB → 'new'."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_result = MagicMock()
        mock_result.first.return_value = None  # no rows

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "new"

    @pytest.mark.asyncio
    async def test_no_api_key_returns_new(self) -> None:
        """When no OpenAI API key is set → skip dedup, return 'new'."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_engine = MagicMock()
        mock_settings = MagicMock()
        mock_settings.openai.api_key = ""

        result = await _check_duplicate(mock_engine, "Some content", mock_settings)
        assert result["status"] == "new"

    @pytest.mark.asyncio
    async def test_exception_returns_new(self) -> None:
        """On any exception → treat as 'new' (fail-open)."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_engine = MagicMock()
        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            MockGen.side_effect = Exception("Connection error")

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "new"

    @pytest.mark.asyncio
    async def test_boundary_exactly_090_is_suspect(self) -> None:
        """sim == 0.90 exactly → 'suspect' (boundary)."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_row = MagicMock()
        mock_row.title = "Boundary article"
        mock_row.similarity = 0.90

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "suspect"

    @pytest.mark.asyncio
    async def test_boundary_exactly_080_is_suspect(self) -> None:
        """sim == 0.80 exactly → 'suspect'."""
        from src.tasks.scraper_tasks import _check_duplicate

        mock_settings = MagicMock()
        mock_settings.openai.api_key = "test-key"
        mock_settings.openai.embedding_model = "text-embedding-3-small"
        mock_settings.openai.embedding_dimensions = 1536

        mock_embedding = [0.1] * 1536

        mock_row = MagicMock()
        mock_row.title = "Boundary article"
        mock_row.similarity = 0.80

        mock_result = MagicMock()
        mock_result.first.return_value = mock_row

        mock_engine = _make_engine_mock(mock_result)

        with patch("src.knowledge.embeddings.EmbeddingGenerator") as MockGen:
            gen_instance = AsyncMock()
            gen_instance.generate = AsyncMock(return_value=[mock_embedding])
            MockGen.return_value = gen_instance

            result = await _check_duplicate(mock_engine, "Some content", mock_settings)

        assert result["status"] == "suspect"


# ─── _get_scraper_config ─────────────────────────────────────


class TestGetScraperConfig:
    """Test config loading with new fields."""

    @pytest.mark.asyncio
    async def test_includes_min_date_from_redis(self) -> None:
        import json

        from src.tasks.scraper_tasks import _get_scraper_config

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(
            return_value=json.dumps({"min_date": "2024-01-01", "dedup_llm_check": True})
        )

        mock_settings = MagicMock()
        mock_settings.scraper.enabled = False
        mock_settings.scraper.base_url = "https://prokoleso.ua"
        mock_settings.scraper.info_path = "/ua/info/"
        mock_settings.scraper.max_pages = 3
        mock_settings.scraper.request_delay = 2.0
        mock_settings.scraper.auto_approve = False
        mock_settings.scraper.llm_model = "claude-haiku-4-5-20251001"
        mock_settings.scraper.min_date = ""
        mock_settings.scraper.dedup_llm_check = False

        config = await _get_scraper_config(mock_redis, mock_settings)

        assert config["min_date"] == "2024-01-01"
        assert config["dedup_llm_check"] is True

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
        mock_settings.scraper.min_date = ""
        mock_settings.scraper.dedup_llm_check = False

        config = await _get_scraper_config(mock_redis, mock_settings)

        assert config["min_date"] == ""
        assert config["dedup_llm_check"] is False
