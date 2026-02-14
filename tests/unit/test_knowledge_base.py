"""Unit tests for knowledge base: chunking, embeddings, and search."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.embeddings import EmbeddingGenerator, chunk_text


class TestChunkText:
    """Test text chunking for embeddings."""

    def test_short_text_single_chunk(self) -> None:
        text = "Short paragraph about tires."
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_paragraphs_split_into_chunks(self) -> None:
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, max_chars=40)
        assert len(chunks) >= 2

    def test_long_paragraph_split_by_sentences(self) -> None:
        # Create a paragraph that exceeds max_chars
        long_para = "This is sentence one. " * 50
        chunks = chunk_text(long_para, max_chars=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 250  # Allow some overflow for sentence boundaries

    def test_empty_text_returns_empty(self) -> None:
        chunks = chunk_text("")
        assert chunks == []

    def test_whitespace_only_returns_empty(self) -> None:
        chunks = chunk_text("   \n\n   ")
        assert chunks == []

    def test_preserves_content(self) -> None:
        text = "Paragraph one about Michelin.\n\nParagraph two about Continental."
        chunks = chunk_text(text)
        combined = " ".join(chunks)
        assert "Michelin" in combined
        assert "Continental" in combined

    def test_merges_small_paragraphs(self) -> None:
        text = "A.\n\nB.\n\nC."
        chunks = chunk_text(text, max_chars=2000)
        # All three fit in one chunk
        assert len(chunks) == 1


class TestEmbeddingGenerator:
    """Test embedding generation with mocked API."""

    @pytest.fixture
    def generator(self) -> EmbeddingGenerator:
        return EmbeddingGenerator(api_key="test-key")

    @pytest.mark.asyncio
    async def test_generate_single(self, generator: EmbeddingGenerator) -> None:
        mock_response = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            ],
        }
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)
        mock_session.post = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        generator._session = mock_session
        result = await generator.generate_single("test query")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_generate_batch(self, generator: EmbeddingGenerator) -> None:
        mock_response = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2]},
                {"index": 1, "embedding": [0.3, 0.4]},
            ],
        }
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)
        mock_session.post = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        generator._session = mock_session
        result = await generator.generate(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2]
        assert result[1] == [0.3, 0.4]

    @pytest.mark.asyncio
    async def test_generate_not_opened_raises(self, generator: EmbeddingGenerator) -> None:
        with pytest.raises(RuntimeError, match="not opened"):
            await generator.generate(["test"])

    @pytest.mark.asyncio
    async def test_api_error_raises(self, generator: EmbeddingGenerator) -> None:
        mock_session = AsyncMock()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_session.post = MagicMock(return_value=AsyncContextManagerMock(mock_resp))

        generator._session = mock_session
        with pytest.raises(RuntimeError, match="Embedding API error 500"):
            await generator.generate(["test"])


class AsyncContextManagerMock:
    """Helper to mock async context managers (async with)."""

    def __init__(self, return_value: AsyncMock) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> AsyncMock:
        return self._return_value

    async def __aexit__(self, *args: object) -> None:
        pass
