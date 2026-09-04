"""Tests for vehicle brand/model transliteration and alias generation.

Wave 8 Phase 2 — seeds vehicle_aliases so customer utterances like «Дастер»,
«Фольксваген Тігуан», «БМВ Ікс5» resolve to (brand_id, model_id) via exact
alias lookup instead of forcing pg_trgm fuzzy match every time.
"""

from __future__ import annotations

import pytest

from src.agent.vehicle_translit import (
    BRAND_CYRILLIC_ALIASES,
    MODEL_CYRILLIC_ALIASES,
    generate_brand_aliases,
    generate_model_aliases,
    normalize_alias,
    translit_lat_to_cyr,
)


class TestNormalizeAlias:
    """The lookup key: lower + ё→е + strip accents + collapse spaces."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Дастер", "дастер"),
            ("ДАСТЕР", "дастер"),
            ("  дастер  ", "дастер"),
            ("Land   Rover", "land rover"),
            ("", ""),
            (" ", ""),
        ],
    )
    def test_basic_cases(self, raw: str, expected: str) -> None:
        assert normalize_alias(raw) == expected

    def test_yo_normalization(self) -> None:
        """ё → е — matches Wave 5 color_detect behaviour."""
        assert normalize_alias("чёрный") == "черный"
        assert normalize_alias("Ёж") == "еж"

    def test_combining_acute_stripped(self) -> None:
        """Bot TTS emphasis (U+0301) collides with plain form."""
        # Cyrillic а + combining acute
        with_accent = "Да" + "́" + "стер"
        assert normalize_alias(with_accent) == "дастер"

    def test_short_i_preserved(self) -> None:
        """й (0x439) must NOT decompose to и + combining brief."""
        assert normalize_alias("черный") == "черный"  # й remains й
        assert normalize_alias("Хёндай") == "хендай"

    def test_idempotent(self) -> None:
        assert normalize_alias(normalize_alias("Фольксваген")) == "фольксваген"


class TestTranslitLatToCyr:
    """Char-by-char fallback for brands not in the hand table."""

    @pytest.mark.parametrize(
        "latin,expected",
        [
            ("Duster", "Дустер"),  # imperfect: real pronunciation "Дастер" comes from MODEL table
            ("Camry", "Камри"),
            ("Focus", "Фокус"),
            ("Corolla", "Королла"),
            ("Foton", "Фотон"),  # brand not in hand table — pure translit
            ("Yaris", "Ярис"),   # 'ya' digram
            ("", ""),
        ],
    )
    def test_char_translit(self, latin: str, expected: str) -> None:
        assert translit_lat_to_cyr(latin) == expected

    def test_digits_pass_through(self) -> None:
        assert translit_lat_to_cyr("X5") == "Кс5"
        assert translit_lat_to_cyr("3008") == "3008"

    def test_capitalisation_preserved(self) -> None:
        """Leading capital preserved for display form."""
        assert translit_lat_to_cyr("Ford")[0].isupper()
        assert translit_lat_to_cyr("ford")[0].islower()


class TestGenerateBrandAliases:
    def test_toyota_hand_curated(self) -> None:
        aliases = generate_brand_aliases("Toyota")
        assert ("Toyota", "auto_import") in aliases
        assert ("Тойота", "auto_translit") in aliases

    def test_hyundai_multiple_variants(self) -> None:
        """Customer might say Хёндай, Хюндай, Хендай, Хундай (dedup by normalized form)."""
        aliases = generate_brand_aliases("Hyundai")
        translit_norms = {normalize_alias(a) for a, s in aliases if s == "auto_translit"}
        # After ё→е dedup: {хендай, хюндай, хундай, хендэ, хьюндай} → 5 distinct
        assert "хендай" in translit_norms
        assert "хюндай" in translit_norms
        assert "хундай" in translit_norms
        assert len(translit_norms) >= 4, translit_norms

    def test_volkswagen_pronunciation(self) -> None:
        """VW → Фольксваген (not char-translit 'Волкшваген')."""
        aliases = generate_brand_aliases("Volkswagen")
        translit_forms = {a for a, s in aliases if s == "auto_translit"}
        assert "Фольксваген" in translit_forms

    def test_fallback_for_unlisted_brand(self) -> None:
        """Foton not in hand table → char-translit adds Фотон."""
        aliases = generate_brand_aliases("Foton")
        norms = {normalize_alias(a) for a, _ in aliases}
        assert "фотон" in norms
        assert "foton" in norms

    def test_no_duplicate_normalized_forms(self) -> None:
        """generate should dedupe identically-normalized aliases."""
        for brand in ["Toyota", "Ford", "Volkswagen", "Hyundai", "BMW"]:
            aliases = generate_brand_aliases(brand)
            norms = [normalize_alias(a) for a, _ in aliases]
            assert len(norms) == len(set(norms)), f"dup in {brand}: {norms}"

    def test_always_includes_source_form(self) -> None:
        """Latin as-is (source='auto_import') is always present."""
        for brand in ["Toyota", "Ford", "Škoda", "Peugeot"]:
            aliases = generate_brand_aliases(brand)
            sources = {s for _, s in aliases}
            assert "auto_import" in sources, brand


class TestGenerateModelAliases:
    def test_duster_pronunciation_variants(self) -> None:
        """Дастер (hand) + Дустер (char translit) both must appear."""
        aliases = generate_model_aliases("Duster")
        forms = {normalize_alias(a) for a, _ in aliases}
        assert "duster" in forms
        assert "дастер" in forms  # from MODEL_CYRILLIC_ALIASES
        assert "дустер" in forms  # from char-translit fallback

    def test_tiguan_ukrainian_variant(self) -> None:
        """Тигуан (default) + Тігуан (uk и→і) both must appear."""
        aliases = generate_model_aliases("Tiguan")
        forms = {normalize_alias(a) for a, _ in aliases}
        assert "тигуан" in forms
        assert "тігуан" in forms

    def test_passat_simplified_consonant(self) -> None:
        """Ukrainian speakers drop double consonants: Пассат → Пасат."""
        aliases = generate_model_aliases("Passat")
        forms = {normalize_alias(a) for a, _ in aliases}
        assert "пассат" in forms
        assert "пасат" in forms  # doubled-consonant simplification

    def test_x5_hand_variants(self) -> None:
        """BMW X5 → Ікс5, Икс5."""
        aliases = generate_model_aliases("X5")
        forms = {normalize_alias(a) for a, _ in aliases}
        assert "x5" in forms
        assert "ікс5" in forms

    def test_short_code_model(self) -> None:
        """3-char model code without translit differences."""
        aliases = generate_model_aliases("500")
        forms = {normalize_alias(a) for a, _ in aliases}
        assert "500" in forms

    def test_no_duplicate_normalized_forms(self) -> None:
        for model in ["Duster", "Tiguan", "Passat", "Focus", "Camry", "X5", "Rio"]:
            aliases = generate_model_aliases(model)
            norms = [normalize_alias(a) for a, _ in aliases]
            assert len(norms) == len(set(norms)), f"dup in {model}: {norms}"


class TestHandTableCoverage:
    """Regression guard: don't accidentally shrink the hand-curated tables."""

    def test_min_brand_count(self) -> None:
        """Should cover at least the top ~70 brands (see WAVE-8 spec)."""
        assert len(BRAND_CYRILLIC_ALIASES) >= 70, len(BRAND_CYRILLIC_ALIASES)

    def test_min_model_count(self) -> None:
        """MODEL_CYRILLIC_ALIASES covers popular tire-shop models."""
        assert len(MODEL_CYRILLIC_ALIASES) >= 50, len(MODEL_CYRILLIC_ALIASES)

    def test_no_empty_variant_lists(self) -> None:
        for brand, variants in BRAND_CYRILLIC_ALIASES.items():
            assert variants, f"{brand} has empty alias list"
        for model, variants in MODEL_CYRILLIC_ALIASES.items():
            assert variants, f"{model} has empty alias list"

    def test_variants_are_cyrillic(self) -> None:
        """Hand-table entries should be Cyrillic (that's the whole point)."""
        for brand, variants in BRAND_CYRILLIC_ALIASES.items():
            for v in variants:
                # Allow ASCII/hyphens/spaces but at least SOME Cyrillic
                assert any(
                    "Ѐ" <= ch <= "ӿ" for ch in v
                ), f"{brand}/{v} has no Cyrillic chars"
