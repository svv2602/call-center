"""Unit tests for article processor translation prompt logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTranslationPrompt:
    """Test that source_language adds translation instructions."""

    def test_language_names_mapping(self) -> None:
        from src.knowledge.article_processor import _LANGUAGE_NAMES

        assert "de" in _LANGUAGE_NAMES
        assert "en" in _LANGUAGE_NAMES
        assert "fr" in _LANGUAGE_NAMES

    def test_translation_addendum_format(self) -> None:
        from src.knowledge.article_processor import _TRANSLATION_ADDENDUM

        rendered = _TRANSLATION_ADDENDUM.format(language_name="German (Deutsch)")
        assert "German (Deutsch)" in rendered
        assert "Ukrainian" in rendered or "українською" in rendered.lower()

    @pytest.mark.asyncio
    async def test_process_article_uk_no_translation(self) -> None:
        """Ukrainian source should NOT add translation addendum."""
        from src.knowledge.article_processor import (
            _SYSTEM_PROMPT,
        )

        captured_system = {}

        async def mock_create(**kwargs):
            captured_system["prompt"] = kwargs.get("system", "")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"is_useful": true, "skip_reason": null, "title": "Test", "category": "general", "content": "Content"}')]
            return mock_resp

        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = mock_create
            mock_cls.return_value = mock_client

            from src.knowledge.article_processor import process_article

            await process_article(
                title="Test",
                content="Content " * 20,
                source_url="https://example.com",
                api_key="test-key",
                source_language="uk",
            )

        assert "translate" not in captured_system["prompt"].lower() or \
            captured_system["prompt"] == _SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_process_article_de_adds_translation(self) -> None:
        """German source should add translation addendum."""
        captured_system = {}

        async def mock_create(**kwargs):
            captured_system["prompt"] = kwargs.get("system", "")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"is_useful": true, "skip_reason": null, "title": "Test", "category": "comparisons", "content": "Зимові шини"}')]
            return mock_resp

        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = mock_create
            mock_cls.return_value = mock_client

            from src.knowledge.article_processor import process_article

            result = await process_article(
                title="Winterreifen-Test",
                content="Der große Test " * 20,
                source_url="https://example.de/test",
                api_key="test-key",
                source_language="de",
            )

        assert "German" in captured_system["prompt"]
        assert "Translate" in captured_system["prompt"] or "translate" in captured_system["prompt"].lower()
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_process_article_en_adds_translation(self) -> None:
        """English source should add translation addendum."""
        captured_system = {}

        async def mock_create(**kwargs):
            captured_system["prompt"] = kwargs.get("system", "")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"is_useful": true, "skip_reason": null, "title": "Test", "category": "comparisons", "content": "Content"}')]
            return mock_resp

        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = mock_create
            mock_cls.return_value = mock_client

            from src.knowledge.article_processor import process_article

            await process_article(
                title="Tire Test",
                content="The big test " * 20,
                source_url="https://example.com/test",
                api_key="test-key",
                source_language="en",
            )

        assert "English" in captured_system["prompt"]

    @pytest.mark.asyncio
    async def test_process_article_default_source_language(self) -> None:
        """Default source_language is 'uk' (no translation)."""
        captured_system = {}

        async def mock_create(**kwargs):
            captured_system["prompt"] = kwargs.get("system", "")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"is_useful": false, "skip_reason": "promo", "title": "T", "category": "general", "content": ""}')]
            return mock_resp

        with patch("anthropic.AsyncAnthropic") as mock_cls:
            mock_client = AsyncMock()
            mock_client.messages.create = mock_create
            mock_cls.return_value = mock_client

            from src.knowledge.article_processor import process_article

            await process_article(
                title="Test",
                content="Content " * 20,
                source_url="https://example.com",
                api_key="test-key",
                # source_language not specified → default "uk"
            )

        # Should not contain translation instructions
        from src.knowledge.article_processor import _TRANSLATION_ADDENDUM

        assert _TRANSLATION_ADDENDUM.split("{")[0].strip() not in captured_system["prompt"]
