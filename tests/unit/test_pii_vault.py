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


class TestAddressContextNotMasked:
    """Street names in addresses must NOT be masked — they are public data."""

    def test_vulytsia_name_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("вулиця Бориса Кротова, 24а")
        assert "Бориса Кротова" in result

    def test_vul_abbreviated_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("вул. Маршала Тимошенка, 7")
        assert "Маршала Тимошенка" in result

    def test_prospekt_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("проспект Дмитра Яворницького, 100")
        assert "Дмитра Яворницького" in result

    def test_shose_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("шосе Запорізьке Кротова, 55К")
        # Single word after шосе — no name match anyway
        assert "Запорізьке" in result

    def test_json_tool_result_address_not_masked(self) -> None:
        vault = PIIVault()
        text = '{"address":"вулиця Бориса Кротова, 24а","customer":"Іван Петренко"}'
        result = vault.mask(text)
        assert "Бориса Кротова" in result  # address preserved
        assert "[NAME_" in result  # customer name masked

    def test_person_name_still_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("Клієнт Бориса Кротова замовив шини")
        assert "Бориса Кротова" not in result
        assert "[NAME_1]" in result

    def test_vul_marshala_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("вул. Маршала Тимошенка — це адреса")
        assert "Маршала Тимошенка" in result  # preceded by "вул."

    def test_provulok_not_masked(self) -> None:
        vault = PIIVault()
        result = vault.mask("провулок Добровольців Перший, 3")
        assert "Добровольців Перший" in result


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
