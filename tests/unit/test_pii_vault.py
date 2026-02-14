"""Unit tests for PIIVault — reversible PII masking."""

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


class TestMaskName:
    def test_mask_name(self) -> None:
        vault = PIIVault()
        result = vault.mask("Мене звати Іван Петренко")
        assert "Іван Петренко" not in result
        assert "[NAME_1]" in result

    def test_mask_name_preserves_surrounding(self) -> None:
        vault = PIIVault()
        result = vault.mask("клієнт Олександр Шевченко замовив")
        assert "клієнт" in result
        assert "замовив" in result
        assert "[NAME_1]" in result


class TestRestoreRoundtrip:
    def test_restore_roundtrip(self) -> None:
        vault = PIIVault()
        original = "Клієнт +380501234567, Іван Петренко, доставка"
        masked = vault.mask(original)
        assert "+380501234567" not in masked
        assert "Іван Петренко" not in masked
        restored = vault.restore(masked)
        assert restored == original

    def test_restore_phone_only(self) -> None:
        vault = PIIVault()
        original = "Телефон: +380671112233"
        masked = vault.mask(original)
        restored = vault.restore(masked)
        assert restored == original


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
    def test_restore_in_args(self) -> None:
        vault = PIIVault()
        vault.mask("+380501234567")
        args = {"phone": "[PHONE_1]", "quantity": 4}
        restored = vault.restore_in_args(args)
        assert restored["phone"] == "+380501234567"
        assert restored["quantity"] == 4

    def test_restore_in_args_nested(self) -> None:
        vault = PIIVault()
        vault.mask("Іван Петренко")
        args = {"customer": {"name": "[NAME_1]", "active": True}}
        restored = vault.restore_in_args(args)
        assert restored["customer"]["name"] == "Іван Петренко"
        assert restored["customer"]["active"] is True

    def test_restore_in_args_list(self) -> None:
        vault = PIIVault()
        vault.mask("+380501234567")
        args = {"phones": ["[PHONE_1]", "other"]}
        restored = vault.restore_in_args(args)
        assert restored["phones"] == ["+380501234567", "other"]


class TestMixedPII:
    def test_mixed_pii(self) -> None:
        vault = PIIVault()
        text = "Іван Петренко, телефон +380501234567, адреса Київ"
        masked = vault.mask(text)
        assert "Іван Петренко" not in masked
        assert "+380501234567" not in masked
        assert "Київ" in masked  # not PII (single word)
        assert "[NAME_1]" in masked
        assert "[PHONE_1]" in masked
        restored = vault.restore(masked)
        assert restored == text
