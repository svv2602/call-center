"""Tests for knowledge base deduplication module."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.dedup import check_semantic_duplicate, check_title_exists

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    for k, v in data.items():
        setattr(row, k, v)
    return row


def _make_mock_engine(
    rows: list[dict[str, Any]],
) -> tuple[Any, AsyncMock]:
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([_make_mock_row(r) for r in rows])
    mock_result.first.return_value = _make_mock_row(rows[0]) if rows else None
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield mock_conn

    mock_engine.begin = _begin
    return mock_engine, mock_conn


class TestCheckTitleExists:
    @pytest.mark.asyncio
    async def test_exact_match(self) -> None:
        engine, _ = _make_mock_engine([{"id": "abc-123", "title": "Test Article"}])
        result = await check_title_exists(engine, "Test Article")
        assert result is not None
        assert result["id"] == "abc-123"
        assert result["title"] == "Test Article"

    @pytest.mark.asyncio
    async def test_case_insensitive(self) -> None:
        engine, conn = _make_mock_engine([{"id": "abc-123", "title": "Test Article"}])
        result = await check_title_exists(engine, "TEST ARTICLE")
        assert result is not None
        # Verify the query was called with lowered title
        call_args = conn.execute.call_args
        assert call_args[0][1]["title"] == "test article"

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        engine, _ = _make_mock_engine([])
        result = await check_title_exists(engine, "Nonexistent Article")
        assert result is None

    @pytest.mark.asyncio
    async def test_exclude_id(self) -> None:
        engine, conn = _make_mock_engine([])
        await check_title_exists(engine, "Test", exclude_id="some-uuid")
        call_args = conn.execute.call_args
        sql_text = str(call_args[0][0])
        assert "exclude_id" in sql_text
        assert call_args[0][1]["exclude_id"] == "some-uuid"


class TestCheckSemanticDuplicate:
    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    async def test_no_api_key(self, mock_settings: MagicMock) -> None:
        mock_settings.return_value.openai.api_key = ""
        engine, _ = _make_mock_engine([])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result == {"status": "new"}

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_duplicate_above_090(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        mock_gen_cls.return_value = mock_gen

        engine, _ = _make_mock_engine([{"title": "Similar Article", "similarity": 0.95}])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result["status"] == "duplicate"
        assert result["similar_title"] == "Similar Article"
        assert result["similarity"] == 0.95

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_suspect_080_to_090(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        mock_gen_cls.return_value = mock_gen

        engine, _ = _make_mock_engine([{"title": "Somewhat Similar", "similarity": 0.85}])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result["status"] == "suspect"
        assert result["similar_title"] == "Somewhat Similar"
        assert result["similarity"] == 0.85

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_new_below_080(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        mock_gen_cls.return_value = mock_gen

        engine, _ = _make_mock_engine([{"title": "Different Article", "similarity": 0.50}])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result == {"status": "new"}

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_no_existing_embeddings(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        mock_gen_cls.return_value = mock_gen

        engine, _ = _make_mock_engine([])  # No rows
        result = await check_semantic_duplicate(engine, "Some content")
        assert result == {"status": "new"}

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_empty_vectors_fallback(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[]])  # Empty vector
        mock_gen_cls.return_value = mock_gen

        engine, _ = _make_mock_engine([])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result == {"status": "new"}

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.get_settings")
    async def test_error_fallback(self, mock_settings: MagicMock) -> None:
        mock_settings.side_effect = RuntimeError("config error")
        engine, _ = _make_mock_engine([])
        result = await check_semantic_duplicate(engine, "Some content")
        assert result == {"status": "new"}

    @pytest.mark.asyncio
    @patch("src.knowledge.dedup.EmbeddingGenerator")
    @patch("src.knowledge.dedup.get_settings")
    async def test_exclude_id(
        self, mock_settings: MagicMock, mock_gen_cls: MagicMock
    ) -> None:
        mock_settings.return_value.openai.api_key = "test-key"
        mock_settings.return_value.openai.embedding_model = "text-embedding-3-small"
        mock_settings.return_value.openai.embedding_dimensions = 1536

        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        mock_gen_cls.return_value = mock_gen

        engine, conn = _make_mock_engine([])
        await check_semantic_duplicate(engine, "Content", exclude_id="my-uuid")
        call_args = conn.execute.call_args
        sql_text = str(call_args[0][0])
        assert "exclude_id" in sql_text
        assert call_args[0][1]["exclude_id"] == "my-uuid"
