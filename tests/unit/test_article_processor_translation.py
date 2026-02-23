"""Unit tests for article processor translation prompt logic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

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
        from src.knowledge.article_processor import _SYSTEM_PROMPT, process_article

        _ok_json = json.dumps(
            {
                "is_useful": True,
                "skip_reason": None,
                "title": "Test",
                "category": "general",
                "content": "Content",
            }
        )
        mock_llm = AsyncMock(return_value=_ok_json)

        with patch("src.knowledge.article_processor.llm_complete", mock_llm):
            await process_article(
                title="Test",
                content="Content " * 20,
                source_url="https://example.com",
                source_language="uk",
            )

        system = mock_llm.call_args.kwargs["system"]
        assert "translate" not in system.lower() or system == _SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_process_article_de_adds_translation(self) -> None:
        """German source should add translation addendum."""
        from src.knowledge.article_processor import process_article

        _ok_json = json.dumps(
            {
                "is_useful": True,
                "skip_reason": None,
                "title": "Test",
                "category": "comparisons",
                "content": "Зимові шини",
            }
        )
        mock_llm = AsyncMock(return_value=_ok_json)

        with patch("src.knowledge.article_processor.llm_complete", mock_llm):
            result = await process_article(
                title="Winterreifen-Test",
                content="Der große Test " * 20,
                source_url="https://example.de/test",
                source_language="de",
            )

        system = mock_llm.call_args.kwargs["system"]
        assert "German" in system
        assert "Translate" in system or "translate" in system.lower()
        assert result.title == "Test"

    @pytest.mark.asyncio
    async def test_process_article_en_adds_translation(self) -> None:
        """English source should add translation addendum."""
        from src.knowledge.article_processor import process_article

        _ok_json = json.dumps(
            {
                "is_useful": True,
                "skip_reason": None,
                "title": "Test",
                "category": "comparisons",
                "content": "Content",
            }
        )
        mock_llm = AsyncMock(return_value=_ok_json)

        with patch("src.knowledge.article_processor.llm_complete", mock_llm):
            await process_article(
                title="Tire Test",
                content="The big test " * 20,
                source_url="https://example.com/test",
                source_language="en",
            )

        system = mock_llm.call_args.kwargs["system"]
        assert "English" in system

    @pytest.mark.asyncio
    async def test_process_article_default_source_language(self) -> None:
        """Default source_language is 'uk' (no translation)."""
        from src.knowledge.article_processor import _TRANSLATION_ADDENDUM, process_article

        _ok_json = json.dumps(
            {
                "is_useful": False,
                "skip_reason": "promo",
                "title": "T",
                "category": "general",
                "content": "",
            }
        )
        mock_llm = AsyncMock(return_value=_ok_json)

        with patch("src.knowledge.article_processor.llm_complete", mock_llm):
            await process_article(
                title="Test",
                content="Content " * 20,
                source_url="https://example.com",
                # source_language not specified → default "uk"
            )

        system = mock_llm.call_args.kwargs["system"]
        # Should not contain translation instructions
        assert _TRANSLATION_ADDENDUM.split("{")[0].strip() not in system
