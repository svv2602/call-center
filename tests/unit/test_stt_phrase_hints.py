"""Tests for STT phrase hints core module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stt.phrase_hints import (
    BRAND_PRONUNCIATIONS,
    extract_catalog_phrases,
    get_all_phrases_flat,
    get_base_phrases,
    get_phrase_hints,
    invalidate_cache,
    refresh_phrase_hints,
    reset_base_to_defaults,
    transliterate_to_cyrillic,
    update_base_phrases,
    update_custom_phrases,
)


class TestGetBasePhrases:
    """Tests for get_base_phrases()."""

    def test_not_empty(self) -> None:
        phrases = get_base_phrases()
        assert len(phrases) > 0

    def test_no_latin_brands(self) -> None:
        """Latin brand keys should NOT be in base phrases (STT outputs Cyrillic)."""
        phrases = get_base_phrases()
        assert "Michelin" not in phrases
        assert "Bridgestone" not in phrases

    def test_contains_cyrillic_pronunciation(self) -> None:
        phrases = get_base_phrases()
        assert "Мішлен" in phrases

    def test_contains_base_terms(self) -> None:
        phrases = get_base_phrases()
        assert "шиномонтаж" in phrases
        assert "шини" in phrases

    def test_contains_store_names(self) -> None:
        phrases = get_base_phrases()
        assert "ПроКолесо" in phrases

    def test_all_pronunciations_in_phrases(self) -> None:
        """All Cyrillic pronunciations should be in base phrases."""
        phrases = get_base_phrases()
        for brand, pronunciations in BRAND_PRONUNCIATIONS.items():
            for pron in pronunciations:
                assert pron in phrases, f"{pron} (from {brand}) not in base phrases"

    def test_all_pronunciations_included(self) -> None:
        phrases = get_base_phrases()
        for pronunciations in BRAND_PRONUNCIATIONS.values():
            for pron in pronunciations:
                assert pron in phrases


class TestTransliterate:
    """Tests for transliterate_to_cyrillic()."""

    def test_blizzak(self) -> None:
        result = transliterate_to_cyrillic("Blizzak")
        assert result is not None
        assert result == "Бліззак"

    def test_pilot_sport_4(self) -> None:
        result = transliterate_to_cyrillic("Pilot Sport 4")
        assert result is not None
        # Numbers preserved, Latin transliterated
        assert "4" in result
        assert result == "Пілот Спорт 4"

    def test_cyrillic_returns_none(self) -> None:
        assert transliterate_to_cyrillic("Росава") is None

    def test_numbers_preserved(self) -> None:
        result = transliterate_to_cyrillic("LM 005")
        assert result is not None
        assert "005" in result

    def test_mixed_case(self) -> None:
        result = transliterate_to_cyrillic("ALL SEASON")
        assert result is not None
        # First letter of each word capitalized
        assert result[0].isupper()

    def test_digraph_sh(self) -> None:
        result = transliterate_to_cyrillic("Shina")
        assert result is not None
        assert result.startswith("Ш")

    def test_digraph_ch(self) -> None:
        result = transliterate_to_cyrillic("Champion")
        assert result is not None
        assert result.startswith("Ч")

    def test_empty_string(self) -> None:
        assert transliterate_to_cyrillic("") is None

    def test_only_numbers(self) -> None:
        assert transliterate_to_cyrillic("12345") is None

    def test_hyphenated(self) -> None:
        result = transliterate_to_cyrillic("X-Ice")
        assert result is not None
        assert "-" in result


class TestExtractCatalogPhrases:
    """Tests for extract_catalog_phrases()."""

    @pytest.mark.asyncio()
    async def test_extracts_manufacturers(self) -> None:
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()

        manufacturer_rows = [("Michelin",), ("Nokian",), ("Росава",)]

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(manufacturer_rows))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        phrases = await extract_catalog_phrases(mock_engine)

        # Latin originals should NOT be in output (STT outputs Cyrillic)
        assert "Michelin" not in phrases
        assert "Nokian" not in phrases

        # Cyrillic originals should be kept as-is
        assert "Росава" in phrases

        # Should contain transliterated Cyrillic variants
        assert "Мічелін" in phrases  # Michelin → Мічелін

        # Only one SQL query (manufacturers only, no models)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio()
    async def test_deduplication(self) -> None:
        mock_engine = AsyncMock()
        mock_conn = AsyncMock()

        # Duplicate manufacturer names (Cyrillic — kept as-is)
        manufacturer_rows = [("Тест",), ("Тест",)]

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(manufacturer_rows))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        phrases = await extract_catalog_phrases(mock_engine)
        # "Тест" should appear only once
        assert phrases.count("Тест") == 1

    @pytest.mark.asyncio()
    async def test_handles_db_error(self) -> None:
        mock_engine = AsyncMock()
        mock_engine.begin = MagicMock(side_effect=RuntimeError("DB error"))

        phrases = await extract_catalog_phrases(mock_engine)
        assert phrases == []


class TestRefreshPhraseHints:
    """Tests for refresh_phrase_hints()."""

    @pytest.mark.asyncio()
    async def test_writes_to_redis(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        invalidate_cache()
        stats = await refresh_phrase_hints(mock_engine, mock_redis)

        mock_redis.set.assert_called_once()
        assert stats["base_count"] > 0
        assert stats["auto_count"] == 0
        assert stats["custom_count"] == 0
        assert "updated_at" in stats

    @pytest.mark.asyncio()
    async def test_preserves_custom_phrases(self) -> None:
        existing = json.dumps({
            "base": ["old"],
            "auto": [],
            "custom": ["my phrase"],
            "updated_at": "2024-01-01",
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        invalidate_cache()
        stats = await refresh_phrase_hints(mock_engine, mock_redis)

        assert stats["custom_count"] == 1
        # Verify saved data includes custom
        saved_call = mock_redis.set.call_args
        saved_data = json.loads(saved_call[0][1])
        assert saved_data["custom"] == ["my phrase"]


class TestGetAllPhrasesFlat:
    """Tests for get_all_phrases_flat()."""

    @pytest.mark.asyncio()
    async def test_returns_tuple(self) -> None:
        data = json.dumps({
            "base": ["Michelin", "Мішлен"],
            "auto": ["Blizzak"],
            "custom": ["мій термін"],
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        invalidate_cache()
        result = await get_all_phrases_flat(mock_redis)

        assert isinstance(result, tuple)
        assert "Michelin" in result
        assert "Мішлен" in result
        assert "Blizzak" in result
        assert "мій термін" in result

    @pytest.mark.asyncio()
    async def test_deduplicates(self) -> None:
        data = json.dumps({
            "base": ["Michelin"],
            "auto": ["michelin"],
            "custom": ["MICHELIN"],
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        invalidate_cache()
        result = await get_all_phrases_flat(mock_redis)

        # Case-insensitive dedup — only the first occurrence kept
        assert len([p for p in result if p.lower() == "michelin"]) == 1

    @pytest.mark.asyncio()
    async def test_cache_works(self) -> None:
        data = json.dumps({"base": ["A"], "auto": [], "custom": []})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        invalidate_cache()
        result1 = await get_all_phrases_flat(mock_redis)
        result2 = await get_all_phrases_flat(mock_redis)

        assert result1 == result2
        # Redis should be called only once (second call uses cache)
        assert mock_redis.get.call_count == 1

    @pytest.mark.asyncio()
    async def test_fallback_base_only_on_empty_redis(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        invalidate_cache()
        result = await get_all_phrases_flat(mock_redis)

        assert len(result) > 0
        assert "Мішлен" in result


class TestUpdateCustomPhrases:
    """Tests for update_custom_phrases()."""

    @pytest.mark.asyncio()
    async def test_replaces_custom_list(self) -> None:
        existing = json.dumps({"base": ["B"], "auto": ["A"], "custom": ["old"]})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        invalidate_cache()
        stats = await update_custom_phrases(mock_redis, ["new1", "new2"])

        assert stats["custom_count"] == 2
        saved_call = mock_redis.set.call_args
        saved_data = json.loads(saved_call[0][1])
        assert saved_data["custom"] == ["new1", "new2"]
        # base and auto preserved
        assert saved_data["base"] == ["B"]
        assert saved_data["auto"] == ["A"]


class TestGetPhraseHints:
    """Tests for get_phrase_hints()."""

    @pytest.mark.asyncio()
    async def test_returns_stats_from_redis(self) -> None:
        data = json.dumps({
            "base": ["a", "b"],
            "auto": ["c"],
            "custom": ["d", "e", "f"],
            "updated_at": "2024-01-01T00:00:00Z",
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        result = await get_phrase_hints(mock_redis)

        assert result["base_count"] == 2
        assert result["auto_count"] == 1
        assert result["custom_count"] == 3
        assert result["total"] == 6
        assert result["updated_at"] == "2024-01-01T00:00:00Z"

    @pytest.mark.asyncio()
    async def test_fallback_on_empty_redis(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        result = await get_phrase_hints(mock_redis)

        assert result["base_count"] > 0
        assert result["auto_count"] == 0
        assert result["custom_count"] == 0
        assert result["updated_at"] is None

    @pytest.mark.asyncio()
    async def test_returns_base_customized_flag(self) -> None:
        data = json.dumps({
            "base": ["a"],
            "auto": [],
            "custom": [],
            "base_customized": True,
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        result = await get_phrase_hints(mock_redis)
        assert result["base_customized"] is True

    @pytest.mark.asyncio()
    async def test_base_customized_defaults_to_false(self) -> None:
        data = json.dumps({"base": ["a"], "auto": [], "custom": []})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=data.encode())

        result = await get_phrase_hints(mock_redis)
        assert result["base_customized"] is False


class TestUpdateBasePhrases:
    """Tests for update_base_phrases()."""

    @pytest.mark.asyncio()
    async def test_updates_base_and_sets_customized(self) -> None:
        existing = json.dumps({"base": ["old"], "auto": ["auto1"], "custom": ["cust1"]})
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        invalidate_cache()
        stats = await update_base_phrases(mock_redis, ["new1", "new2"])

        assert stats["base_count"] == 2
        assert stats["base_customized"] is True
        # auto and custom preserved
        assert stats["auto_count"] == 1
        assert stats["custom_count"] == 1

        saved_data = json.loads(mock_redis.set.call_args[0][1])
        assert saved_data["base"] == ["new1", "new2"]
        assert saved_data["auto"] == ["auto1"]
        assert saved_data["custom"] == ["cust1"]
        assert saved_data["base_customized"] is True

    @pytest.mark.asyncio()
    async def test_works_on_empty_redis(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()

        invalidate_cache()
        stats = await update_base_phrases(mock_redis, ["phrase1"])

        assert stats["base_count"] == 1
        assert stats["base_customized"] is True


class TestResetBaseToDefaults:
    """Tests for reset_base_to_defaults()."""

    @pytest.mark.asyncio()
    async def test_resets_to_hardcoded_defaults(self) -> None:
        existing = json.dumps({
            "base": ["custom1"],
            "auto": ["auto1"],
            "custom": ["cust1"],
            "base_customized": True,
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        invalidate_cache()
        stats = await reset_base_to_defaults(mock_redis)

        assert stats["base_customized"] is False
        assert stats["base_count"] == len(get_base_phrases())
        # auto and custom preserved
        assert stats["auto_count"] == 1
        assert stats["custom_count"] == 1

        saved_data = json.loads(mock_redis.set.call_args[0][1])
        assert saved_data["base"] == get_base_phrases()
        assert saved_data["base_customized"] is False


class TestRefreshPreservesCustomBase:
    """Tests that refresh preserves customized base."""

    @pytest.mark.asyncio()
    async def test_preserves_custom_base_on_refresh(self) -> None:
        custom_base = ["MyCustomBrand", "MyCustomTerm"]
        existing = json.dumps({
            "base": custom_base,
            "auto": ["old_auto"],
            "custom": ["my phrase"],
            "base_customized": True,
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        invalidate_cache()
        stats = await refresh_phrase_hints(mock_engine, mock_redis)

        # Custom base should be preserved
        saved_data = json.loads(mock_redis.set.call_args[0][1])
        assert saved_data["base"] == custom_base
        assert saved_data["base_customized"] is True
        assert stats["custom_count"] == 1

    @pytest.mark.asyncio()
    async def test_uses_default_base_when_not_customized(self) -> None:
        existing = json.dumps({
            "base": ["old_base"],
            "auto": [],
            "custom": [],
            "base_customized": False,
        })
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=existing.encode())
        mock_redis.set = AsyncMock()

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        invalidate_cache()
        await refresh_phrase_hints(mock_engine, mock_redis)

        saved_data = json.loads(mock_redis.set.call_args[0][1])
        assert saved_data["base"] == get_base_phrases()
        assert saved_data["base_customized"] is False
