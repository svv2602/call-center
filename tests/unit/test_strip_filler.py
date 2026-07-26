"""Tests for pipeline._strip_filler: remove filler/duplicate sentences from
LLM output before TTS.
"""

from __future__ import annotations

from src.core.pipeline import _strip_filler


class TestStripFiller:
    def test_empty_input(self) -> None:
        assert _strip_filler("") == ("", [])
        assert _strip_filler("   ") == ("   ", [])

    def test_no_filler(self) -> None:
        text = "На яку дату записуємо?"
        cleaned, stripped = _strip_filler(text)
        assert cleaned == text
        assert stripped == []

    def test_strip_zaraz_perevirju_prefix(self) -> None:
        text = (
            "Зараз перевірю вільні слоти на 28 липня. "
            "Вільний час: 9:00, 9:40, 10:20."
        )
        cleaned, stripped = _strip_filler(text)
        assert cleaned == "Вільний час: 9:00, 9:40, 10:20."
        assert len(stripped) == 1
        assert stripped[0][0].startswith("opener:зараз перевірю")

    def test_strip_todi_perevirju(self) -> None:
        text = (
            "Тоді перевірю вільні слоти на 28 липня 2026 року. "
            "Отже, вівторок, 28 липня. Вільний час 9:00."
        )
        cleaned, stripped = _strip_filler(text)
        assert "Тоді перевірю" not in cleaned
        assert "Отже, вівторок, 28 липня." in cleaned
        assert any(p.startswith("opener:тоді перевірю") for p, _ in stripped)

    def test_strip_chekayte_bud_laska_mid(self) -> None:
        text = (
            "Зрозуміла, обираємо вівторок. "
            "Чекайте, будь ласка. "
            "Вільний час 9:00, 9:40."
        )
        cleaned, stripped = _strip_filler(text)
        assert "Чекайте" not in cleaned
        assert "Зрозуміла" in cleaned
        assert "Вільний час 9:00, 9:40." in cleaned
        assert any("чекайте" in p for p, _ in stripped)

    def test_strip_sekundochku(self) -> None:
        text = "Секундочку. Знайшла у профілі Volkswagen Tiguan."
        cleaned, stripped = _strip_filler(text)
        assert cleaned == "Знайшла у профілі Volkswagen Tiguan."
        assert len(stripped) == 1
        assert stripped[0][0] == "opener:секундочку"

    def test_double_confirm_collapse(self) -> None:
        text = (
            "Зрозуміла, обираємо вівторок. "
            "Отже, вівторок, 28 липня. "
            "Вільний час 9:00, 9:40."
        )
        cleaned, stripped = _strip_filler(text)
        # First sentence dropped, second kept
        assert cleaned.startswith("Отже, вівторок, 28 липня.")
        assert "Зрозуміла" not in cleaned
        assert any(p == "double_confirm" for p, _ in stripped)

    def test_full_transcript_turn6_realworld(self) -> None:
        """Real bot reply from call 2f19bcdb (2026-07-26): 4 sentences → 1."""
        text = (
            "Зрозуміла, обираємо вівторок. "
            "Тоді перевірю вільні слоти на 28 липня 2026 року. "
            "Чекайте, будь ласка. "
            "Отже, вівторок, 28 липня. Вільний час о 9:00, 9:40, 10:20."
        )
        cleaned, stripped = _strip_filler(text)
        assert "Тоді перевірю" not in cleaned
        assert "Чекайте" not in cleaned
        assert "Зрозуміла" not in cleaned
        # What remains: only "Отже..." + the slots list
        assert cleaned.startswith("Отже, вівторок, 28 липня.")
        assert "9:00" in cleaned
        # 3 patterns stripped: todi perevirju + chekayte + double_confirm
        assert len(stripped) == 3

    def test_zrozumila_alone_kept(self) -> None:
        """'Зрозуміла' by itself is fine — only collapse when 'Отже' follows."""
        text = "Зрозуміла, обираємо вівторок. Далі, назвіть держномер."
        cleaned, stripped = _strip_filler(text)
        assert cleaned == text
        assert stripped == []

    def test_case_insensitive_matching(self) -> None:
        text = "ЗАРАЗ ПЕРЕВІРЮ слоти. Вільний час 9:00."
        cleaned, stripped = _strip_filler(text)
        assert "ЗАРАЗ" not in cleaned
        assert cleaned == "Вільний час 9:00."
        assert len(stripped) == 1

    def test_never_returns_empty_when_only_filler(self) -> None:
        """When ALL sentences are filler, return cleaned='' + all stripped.
        Caller is responsible for falling back to original.
        """
        text = "Зараз перевірю. Чекайте, будь ласка."
        cleaned, stripped = _strip_filler(text)
        assert cleaned == ""
        assert len(stripped) == 2

    def test_legitimate_content_with_perevirju_word(self) -> None:
        """'Перевіримо' (future 1pl) is different from 'Перевіряю' — not stripped."""
        text = "Перевіримо ваш номер разом: 0501234567. Так?"
        cleaned, stripped = _strip_filler(text)
        assert cleaned == text
        assert stripped == []

    def test_single_sentence(self) -> None:
        text = "На яку дату?"
        cleaned, stripped = _strip_filler(text)
        assert cleaned == text
        assert stripped == []
