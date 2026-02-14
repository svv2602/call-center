"""Unit tests for PII sanitizer."""

from src.logging.pii_sanitizer import sanitize_phone, sanitize_pii


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


class TestPIISanitization:
    """Test full PII sanitization."""

    def test_sanitize_pii_masks_phone(self) -> None:
        text = "Дзвінок від +380501234567"
        result = sanitize_pii(text)
        assert "1234567" not in result
