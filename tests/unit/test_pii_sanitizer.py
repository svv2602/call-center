"""Unit tests for PII sanitizer."""

from src.logging.pii_sanitizer import (
    sanitize_address,
    sanitize_card,
    sanitize_email,
    sanitize_iban,
    sanitize_name,
    sanitize_phone,
    sanitize_pii,
)


class TestPhoneSanitization:
    """Test phone number masking."""

    def test_mask_ua_phone_with_plus(self) -> None:
        result = sanitize_phone("+380501234567")
        assert "123" not in result
        assert "+380" in result

    def test_mask_ua_phone_without_plus(self) -> None:
        result = sanitize_phone("380501234567")
        assert "123" not in result

    def test_mask_phone_in_text(self) -> None:
        text = "Клієнт +380671234567 дзвонив"
        result = sanitize_phone(text)
        assert "+38067" in result
        assert "1234567" not in result

    def test_no_phone_unchanged(self) -> None:
        text = "Просто текст без номера"
        assert sanitize_phone(text) == text


class TestNameSanitization:
    """Test name masking."""

    def test_mask_cyrillic_name(self) -> None:
        result = sanitize_name("Іван Петренко")
        assert result == "І*** П***"

    def test_mask_name_in_text(self) -> None:
        result = sanitize_name("дзвонив Олександр Шевченко сьогодні")
        assert "Олександр" not in result
        assert "Шевченко" not in result
        assert "О***" in result
        assert "Ш***" in result
        assert "сьогодні" in result

    def test_short_words_not_masked(self) -> None:
        # Words shorter than 3 chars after the capital letter shouldn't match
        text = "Ой Ні"
        assert sanitize_name(text) == text

    def test_lowercase_not_masked(self) -> None:
        text = "просто текст"
        assert sanitize_name(text) == text


class TestEmailSanitization:
    """Test email masking."""

    def test_mask_simple_email(self) -> None:
        result = sanitize_email("user@example.com")
        assert result == "u***@***.com"

    def test_mask_email_in_text(self) -> None:
        result = sanitize_email("Email клієнта: john.doe@gmail.com для зв'язку")
        assert "john.doe" not in result
        assert "gmail" not in result
        assert "j***@***.com" in result
        assert "для зв'язку" in result

    def test_mask_email_with_numbers(self) -> None:
        result = sanitize_email("test123@company.ua")
        assert "test123" not in result
        assert "t***@***.ua" in result

    def test_no_email_unchanged(self) -> None:
        text = "Просто текст без email"
        assert sanitize_email(text) == text


class TestCardSanitization:
    """Test card number masking."""

    def test_mask_card_with_spaces(self) -> None:
        result = sanitize_card("4111 1111 1111 1111")
        assert result == "4111 **** **** 1111"

    def test_mask_card_without_spaces(self) -> None:
        result = sanitize_card("4111111111111111")
        assert result == "4111 **** **** 1111"

    def test_mask_card_with_dashes(self) -> None:
        result = sanitize_card("5500-0000-0000-0004")
        assert result == "5500 **** **** 0004"

    def test_mask_card_in_text(self) -> None:
        result = sanitize_card("Картка: 4111 1111 1111 1111, дякую")
        assert "4111 **** **** 1111" in result
        assert "дякую" in result

    def test_no_card_unchanged(self) -> None:
        text = "Просто текст без картки"
        assert sanitize_card(text) == text


class TestAddressSanitization:
    """Test address masking."""

    def test_mask_vul_address(self) -> None:
        result = sanitize_address("вул. Хрещатик, 22")
        assert "Хрещатик" not in result
        assert "22" not in result
        assert "вул." in result
        assert "***" in result

    def test_mask_ul_address(self) -> None:
        result = sanitize_address("ул. Пушкина, д. 15")
        assert "Пушкина" not in result
        assert "15" not in result

    def test_mask_pr_address(self) -> None:
        result = sanitize_address("пр. Шевченка, 100")
        assert "Шевченка" not in result
        assert "100" not in result

    def test_mask_bulv_address(self) -> None:
        result = sanitize_address("бульв. Лесі Українки, 5а")
        assert "Лесі Українки" not in result
        assert "5а" not in result

    def test_mask_address_in_text(self) -> None:
        result = sanitize_address("Доставка на вул. Хрещатик, 22 завтра")
        assert "Хрещатик" not in result
        assert "завтра" in result

    def test_no_address_unchanged(self) -> None:
        text = "Просто текст без адреси"
        assert sanitize_address(text) == text


class TestIBANSanitization:
    """Test IBAN masking."""

    def test_mask_iban_no_spaces(self) -> None:
        result = sanitize_iban("UA213223130000026007233566001")
        assert "UA21" in result
        assert "6001" in result
        assert "32231300" not in result
        assert "****" in result

    def test_mask_iban_with_spaces(self) -> None:
        result = sanitize_iban("UA21 3223 1300 0002 6007 2335 66001")
        assert "UA21" in result
        assert "6001" in result
        assert "3223" not in result

    def test_mask_iban_in_text(self) -> None:
        result = sanitize_iban("Рахунок: UA213223130000026007233566001 для оплати")
        assert "UA21" in result
        assert "32231300" not in result
        assert "для оплати" in result

    def test_no_iban_unchanged(self) -> None:
        text = "Просто текст без IBAN"
        assert sanitize_iban(text) == text


class TestPIISanitization:
    """Test full PII sanitization."""

    def test_sanitize_pii_masks_phone(self) -> None:
        text = "Дзвінок від +380501234567"
        result = sanitize_pii(text)
        assert "1234567" not in result

    def test_sanitize_pii_masks_name(self) -> None:
        text = "дзвонив Іван Петренко"
        result = sanitize_pii(text)
        assert "Іван" not in result
        assert "Петренко" not in result
        assert "І***" in result

    def test_sanitize_pii_masks_email(self) -> None:
        result = sanitize_pii("Email: user@example.com")
        assert "user" not in result
        assert "example" not in result

    def test_sanitize_pii_masks_card(self) -> None:
        result = sanitize_pii("Картка: 4111 1111 1111 1111")
        assert "4111 **** **** 1111" in result

    def test_sanitize_pii_masks_address(self) -> None:
        result = sanitize_pii("Адреса: вул. Хрещатик, 22")
        assert "Хрещатик" not in result

    def test_sanitize_pii_masks_iban(self) -> None:
        result = sanitize_pii("IBAN: UA213223130000026007233566001")
        assert "32231300" not in result
        assert "UA21" in result

    def test_sanitize_pii_masks_multiple(self) -> None:
        text = "Іван Петренко, +380501234567, user@example.com, вул. Хрещатик, 22"
        result = sanitize_pii(text)
        assert "Іван" not in result
        assert "Петренко" not in result
        assert "1234567" not in result
        assert "user" not in result
        assert "Хрещатик" not in result
