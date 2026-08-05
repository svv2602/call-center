"""Unit tests for src/stt/entity_normalizer.py."""

import pytest

from src.stt.entity_normalizer import normalize_brand_names, normalize_entities, normalize_tire_sizes


class TestNormalizeTireSizes:
    def test_space_separator_with_r(self):
        assert normalize_tire_sizes("205 55 R16")[0] == "205/55 R16"

    def test_space_separator_no_r(self):
        assert normalize_tire_sizes("205 55 16")[0] == "205/55 R16"

    def test_slash_no_space_before_r(self):
        assert normalize_tire_sizes("205/55R16")[0] == "205/55 R16"

    def test_lowercase_r(self):
        assert normalize_tire_sizes("205/55 r16")[0] == "205/55 R16"

    def test_already_canonical(self):
        text = "205/55 R16"
        result, n = normalize_tire_sizes(text)
        assert result == "205/55 R16"

    def test_in_sentence(self):
        result, n = normalize_tire_sizes("хочу шини 245 45 R18 літні")
        assert "245/45 R18" in result
        assert n == 1

    def test_multiple_sizes(self):
        result, n = normalize_tire_sizes("205 55 16 або 215 55 16")
        assert n == 2
        assert "205/55 R16" in result
        assert "215/55 R16" in result

    def test_no_match_random_numbers(self):
        _, n = normalize_tire_sizes("замовлення 12345 від 10 05 24")
        assert n == 0

    def test_diameter_boundary_13(self):
        result, n = normalize_tire_sizes("165 70 13")
        assert "165/70 R13" in result

    def test_diameter_boundary_24(self):
        result, n = normalize_tire_sizes("275 35 24")
        assert "275/35 R24" in result

    def test_diameter_outside_range(self):
        # 25 is not a valid rim diameter — should not match
        _, n = normalize_tire_sizes("205 55 25")
        assert n == 0

    def test_count_returned(self):
        _, n = normalize_tire_sizes("немає розміру")
        assert n == 0
        _, n = normalize_tire_sizes("205 55 16")
        assert n == 1


class TestNormalizeBrandNames:
    @pytest.mark.parametrize("phonetic,expected", [
        ("Мішлен", "Michelin"),
        ("мішлен", "Michelin"),
        ("Мишлен", "Michelin"),
        ("Мічелін", "Michelin"),
        ("Бріджстоун", "Bridgestone"),
        ("Бриджстоун", "Bridgestone"),
        ("Брідстоун", "Bridgestone"),
        ("Контіненталь", "Continental"),
        ("Континенталь", "Continental"),
        ("Піреллі", "Pirelli"),
        ("Піреліі", "Pirelli"),
        ("Ханкук", "Hankook"),
        ("Ганкук", "Hankook"),
        ("Нокіан", "Nokian"),
        ("Гудйір", "Goodyear"),
        ("Гудьєр", "Goodyear"),
        ("Данлоп", "Dunlop"),
        ("Дунлоп", "Dunlop"),
        ("Фалькен", "Falken"),
        ("Тойо", "Toyo"),
        ("Йокогама", "Yokohama"),
        ("Йокохама", "Yokohama"),
        ("Кумхо", "Kumho"),
        ("Нексен", "Nexen"),
        ("Максіс", "Maxxis"),
    ])
    def test_brand_alias(self, phonetic, expected):
        result, n = normalize_brand_names(phonetic)
        assert result == expected
        assert n == 1

    def test_in_sentence(self):
        result, n = normalize_brand_names("хочу Мішлен або Контіненталь")
        assert "Michelin" in result
        assert "Continental" in result
        assert n == 2

    def test_no_false_positive(self):
        _, n = normalize_brand_names("записатись на монтаж")
        assert n == 0

    def test_case_insensitive(self):
        result, _ = normalize_brand_names("МІШЛЕН")
        assert result == "Michelin"


class TestNormalizeEntities:
    def test_tire_size_and_brand_together(self):
        result, n = normalize_entities("хочу Мішлен 205 55 R16", None)
        assert "Michelin" in result
        assert "205/55 R16" in result
        assert n == 2

    def test_skips_tire_size_in_date_context(self):
        result, n = normalize_entities("205 55 16", "date")
        assert "205 55 16" == result  # not normalized
        assert n == 0

    def test_skips_tire_size_in_time_context(self):
        result, n = normalize_entities("205 55 16", "time")
        assert result == "205 55 16"
        assert n == 0

    def test_brand_normalized_in_date_context(self):
        # brands always safe
        result, n = normalize_entities("Мішлен", "date")
        assert result == "Michelin"
        assert n == 1

    def test_empty_text(self):
        result, n = normalize_entities("", None)
        assert result == ""
        assert n == 0

    def test_plate_context_allows_tire_size(self):
        # plate context should still normalize tire sizes
        result, n = normalize_entities("і шини 205 55 16", "plate")
        assert "205/55 R16" in result
