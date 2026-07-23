"""Unit tests for PIIVault — reversible PII masking (phones only).

Names are intentionally NOT masked: masking them made LLMs (Gemini 2.5 Flash)
drift into meta-text about the `[NAME_1]` placeholder and, in the worst
case, emit raw tool_code pseudo-Python that was spoken to the caller
(call 43a4b637, 2026-07-23).
"""

from src.logging.pii_vault import PIIVault


class TestMaskPhone:
    def test_mask_phone(self) -> None:
        vault = PIIVault()
        result = vault.mask("Дзвоню з +380501234567")
        assert "+380501234567" not in result
        assert "[PHONE_1]" in result

    def test_mask_phone_in_context(self) -> None:
        vault = PIIVault()
        result = vault.mask("Номер клієнта 380671234567, запишіть")
        assert "380671234567" not in result
        assert "[PHONE_1]" in result


class TestNamesNotMasked:
    """Names pass through unchanged — LLM sees the real name."""

    def test_name_intro_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("Мене звати Іван Петренко")
        assert "Іван Петренко" in result
        assert "[NAME_" not in result

    def test_name_in_context_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("клієнт Олександр Шевченко замовив")
        assert "Олександр Шевченко" in result
        assert "[NAME_" not in result

    def test_first_name_only_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("Вася Василий")
        assert "Вася Василий" in result
        assert "[NAME_" not in result


class TestAddressesNotMasked:
    """Street names must NOT be masked — they are public data."""

    def test_street_with_vulytsia(self) -> None:
        vault = PIIVault()
        result = vault.mask("вулиця Бориса Кротова, 24а")
        assert "Бориса Кротова" in result

    def test_colloquial_address(self) -> None:
        vault = PIIVault()
        result = vault.mask("монтаж на Героев Днепра")
        assert "Героев Днепра" in result


class TestVehicleBrandsNotMasked:
    def test_volkswagen_tiguan(self) -> None:
        vault = PIIVault()
        result = vault.mask("Volkswagen Tiguan")
        assert "Volkswagen Tiguan" in result

    def test_vehicle_owner_name_also_passes_through(self) -> None:
        vault = PIIVault()
        result = vault.mask("Volkswagen Tiguan, власник Іван Петренко")
        assert "Volkswagen Tiguan" in result
        assert "Іван Петренко" in result
        assert "[NAME_" not in result


class TestRestoreRoundtrip:
    def test_restore_phone_roundtrip(self) -> None:
        vault = PIIVault()
        original = "Клієнт +380501234567, доставка"
        masked = vault.mask(original)
        assert "+380501234567" not in masked
        restored = vault.restore(masked)
        assert restored == original

    def test_restore_phone_only(self) -> None:
        vault = PIIVault()
        original = "Телефон: +380671112233"
        masked = vault.mask(original)
        restored = vault.restore(masked)
        assert restored == original

    def test_text_with_name_and_phone(self) -> None:
        vault = PIIVault()
        original = "Іван Петренко, тел. +380501234567"
        masked = vault.mask(original)
        # name preserved, phone masked
        assert "Іван Петренко" in masked
        assert "+380501234567" not in masked
        assert "[PHONE_1]" in masked
        assert vault.restore(masked) == original


class TestSamePhoneReusesPlaceholder:
    def test_same_phone_reuses_placeholder(self) -> None:
        vault = PIIVault()
        vault.mask("Номер +380501234567")
        result = vault.mask("Повторюю +380501234567")
        assert result.count("[PHONE_1]") == 1
        assert "[PHONE_2]" not in result

    def test_different_phones_get_different_placeholders(self) -> None:
        vault = PIIVault()
        result = vault.mask("+380501234567 та +380671112233")
        assert "[PHONE_1]" in result
        assert "[PHONE_2]" in result


class TestRestoreInArgs:
    def test_restore_phone_in_args(self) -> None:
        vault = PIIVault()
        vault.mask("+380501234567")
        args = {"phone": "[PHONE_1]", "quantity": 4}
        restored = vault.restore_in_args(args)
        assert restored["phone"] == "+380501234567"
        assert restored["quantity"] == 4

    def test_restore_in_args_nested(self) -> None:
        vault = PIIVault()
        vault.mask("+380501234567")
        args = {"customer": {"phone": "[PHONE_1]", "active": True}}
        restored = vault.restore_in_args(args)
        assert restored["customer"]["phone"] == "+380501234567"
        assert restored["customer"]["active"] is True

    def test_restore_in_args_list(self) -> None:
        vault = PIIVault()
        vault.mask("+380501234567")
        args = {"phones": ["[PHONE_1]", "other"]}
        restored = vault.restore_in_args(args)
        assert restored["phones"] == ["+380501234567", "other"]

    def test_name_arg_passes_through(self) -> None:
        vault = PIIVault()
        vault.mask("Іван Петренко")  # no-op — names not masked
        args = {"name": "Іван Петренко", "phone": "[PHONE_1]"}
        vault.mask("+380501234567")
        restored = vault.restore_in_args(args)
        assert restored["name"] == "Іван Петренко"
        assert restored["phone"] == "+380501234567"
