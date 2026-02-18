"""Unit tests for LLM article processor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.article_processor import (
    ProcessedArticle,
    _MAX_CONTENT_CHARS,
    _validate_category,
    process_article,
)


# ─── Category validation ─────────────────────────────────────


class TestValidateCategory:
    """Test category validation and normalization."""

    def test_valid_categories(self) -> None:
        for cat in ["brands", "guides", "faq", "comparisons", "policies",
                     "procedures", "returns", "warranty", "delivery", "general"]:
            assert _validate_category(cat) == cat

    def test_uppercase_normalized(self) -> None:
        assert _validate_category("GUIDES") == "guides"
        assert _validate_category("FAQ") == "faq"

    def test_mixed_case(self) -> None:
        assert _validate_category("Comparisons") == "comparisons"

    def test_whitespace_stripped(self) -> None:
        assert _validate_category("  guides  ") == "guides"

    def test_invalid_defaults_to_general(self) -> None:
        assert _validate_category("unknown") == "general"
        assert _validate_category("") == "general"
        assert _validate_category("tyres") == "general"

    def test_none_defaults_to_general(self) -> None:
        assert _validate_category(None) == "general"


# ─── LLM response parsing ────────────────────────────────────


def _make_llm_response(text: str) -> MagicMock:
    """Create a mock Anthropic message response."""
    content_block = MagicMock()
    content_block.text = text

    response = MagicMock()
    response.content = [content_block]
    return response


class TestProcessArticle:
    """Test article processing with mocked LLM."""

    @pytest.mark.asyncio
    async def test_useful_article(self) -> None:
        llm_json = json.dumps({
            "is_useful": True,
            "skip_reason": None,
            "title": "Як обрати зимові шини",
            "category": "guides",
            "content": "## Вибір зимових шин\n\nВажливо враховувати температуру.",
        })

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(llm_json))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Як обрати зимові шини — поради",
                content="Довгий текст про вибір шин...",
                source_url="https://prokoleso.ua/ua/info/guides/winter-tires/",
                api_key="test-key",
            )

        assert isinstance(result, ProcessedArticle)
        assert result.is_useful is True
        assert result.skip_reason is None
        assert result.title == "Як обрати зимові шини"
        assert result.category == "guides"
        assert "Вибір зимових шин" in result.content

    @pytest.mark.asyncio
    async def test_not_useful_article(self) -> None:
        llm_json = json.dumps({
            "is_useful": False,
            "skip_reason": "Purely promotional content",
            "title": "Акція на шини",
            "category": "general",
            "content": "",
        })

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(llm_json))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Акція!!! -50% на всі шини",
                content="Купуйте зараз!",
                source_url="https://prokoleso.ua/ua/info/promo/",
                api_key="test-key",
            )

        assert result.is_useful is False
        assert result.skip_reason == "Purely promotional content"

    @pytest.mark.asyncio
    async def test_invalid_category_falls_back(self) -> None:
        llm_json = json.dumps({
            "is_useful": True,
            "skip_reason": None,
            "title": "Test",
            "category": "invalid_category",
            "content": "Some content",
        })

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(llm_json))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Test", content="Content",
                source_url="https://example.com", api_key="test-key",
            )

        assert result.category == "general"

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fences(self) -> None:
        llm_json = json.dumps({
            "is_useful": True,
            "skip_reason": None,
            "title": "Cleaned",
            "category": "faq",
            "content": "FAQ content",
        })
        fenced = f"```json\n{llm_json}\n```"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(fenced))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Test", content="Content",
                source_url="https://example.com", api_key="test-key",
            )

        assert result.is_useful is True
        assert result.title == "Cleaned"
        assert result.category == "faq"

    @pytest.mark.asyncio
    async def test_strips_bare_backtick_fences(self) -> None:
        llm_json = json.dumps({
            "is_useful": True,
            "skip_reason": None,
            "title": "Test",
            "category": "guides",
            "content": "Bare fenced",
        })
        fenced = f"```\n{llm_json}\n```"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(fenced))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Test", content="Content",
                source_url="https://example.com", api_key="test-key",
            )

        assert result.is_useful is True
        assert result.content == "Bare fenced"

    @pytest.mark.asyncio
    async def test_invalid_json_fallback(self) -> None:
        """When LLM returns invalid JSON, fall back to useful with original content."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_llm_response("This is not valid JSON at all")
        )

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Original Title",
                content="Original content that should be preserved",
                source_url="https://example.com",
                api_key="test-key",
            )

        assert result.is_useful is True
        assert result.title == "Original Title"
        assert result.category == "general"
        assert "Original content" in result.content

    @pytest.mark.asyncio
    async def test_api_error_propagates(self) -> None:
        """Non-JSON exceptions should propagate up."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=RuntimeError("API connection failed")
        )

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API connection failed"):
                await process_article(
                    title="Test", content="Content",
                    source_url="https://example.com", api_key="test-key",
                )

    @pytest.mark.asyncio
    async def test_content_truncation(self) -> None:
        """Long content should be truncated before sending to LLM."""
        long_content = "A" * 20000

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_llm_response(json.dumps({
                "is_useful": True,
                "skip_reason": None,
                "title": "Test",
                "category": "general",
                "content": "Cleaned",
            }))
        )

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            await process_article(
                title="Test", content=long_content,
                source_url="https://example.com", api_key="test-key",
            )

        # Verify the user message sent to LLM was truncated
        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "[... content truncated ...]" in user_msg
        # Should not contain the full 20k chars
        assert len(user_msg) < 20000

    @pytest.mark.asyncio
    async def test_short_content_not_truncated(self) -> None:
        """Short content should not have truncation marker."""
        short_content = "Short article about tires."

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_llm_response(json.dumps({
                "is_useful": True,
                "skip_reason": None,
                "title": "Test",
                "category": "general",
                "content": "Cleaned",
            }))
        )

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            await process_article(
                title="Test", content=short_content,
                source_url="https://example.com", api_key="test-key",
            )

        call_kwargs = mock_client.messages.create.call_args
        user_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "[... content truncated ...]" not in user_msg

    @pytest.mark.asyncio
    async def test_missing_fields_use_defaults(self) -> None:
        """LLM response with missing fields should use fallback values."""
        llm_json = json.dumps({"is_useful": True})

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_llm_response(llm_json))

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            result = await process_article(
                title="Fallback Title",
                content="Content",
                source_url="https://example.com",
                api_key="test-key",
            )

        assert result.is_useful is True
        assert result.title == "Fallback Title"  # falls back to original title
        assert result.category == "general"  # falls back to general
        assert result.content == ""  # empty content default

    @pytest.mark.asyncio
    async def test_model_parameter_passed(self) -> None:
        """Custom model name should be passed to the API."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_llm_response(json.dumps({
                "is_useful": True, "skip_reason": None,
                "title": "T", "category": "general", "content": "C",
            }))
        )

        with patch("src.knowledge.article_processor.anthropic.AsyncAnthropic", return_value=mock_client):
            await process_article(
                title="Test", content="Content",
                source_url="https://example.com",
                api_key="test-key",
                model="claude-sonnet-4-5-20250929",
            )

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-5-20250929"
