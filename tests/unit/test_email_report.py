"""Unit tests for weekly email report task."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks.email_report import _generate_and_send_weekly_report, _send_email


class TestSendEmail:
    """Test _send_email function."""

    @pytest.mark.asyncio
    @patch("src.tasks.email_report.aiosmtplib")
    @patch("src.tasks.email_report.get_settings")
    async def test_sends_email_with_attachment(
        self, mock_settings: MagicMock, mock_smtp: MagicMock
    ) -> None:
        mock_smtp.send = AsyncMock()
        smtp_cfg = MagicMock()
        smtp_cfg.from_address = "bot@example.com"
        smtp_cfg.host = "smtp.example.com"
        smtp_cfg.port = 587
        smtp_cfg.user = "user"
        smtp_cfg.password = "pass"
        smtp_cfg.use_tls = True
        mock_settings.return_value.smtp = smtp_cfg

        await _send_email(
            subject="Test Report",
            body="Here is the report.",
            recipients=["admin@example.com"],
            attachment=b"%PDF-1.4 test",
            attachment_filename="report.pdf",
        )

        mock_smtp.send.assert_called_once()
        call_args = mock_smtp.send.call_args
        msg = call_args[0][0]
        assert msg["Subject"] == "Test Report"
        assert msg["To"] == "admin@example.com"

    @pytest.mark.asyncio
    @patch("src.tasks.email_report.aiosmtplib")
    @patch("src.tasks.email_report.get_settings")
    async def test_sends_without_attachment(
        self, mock_settings: MagicMock, mock_smtp: MagicMock
    ) -> None:
        mock_smtp.send = AsyncMock()
        smtp_cfg = MagicMock()
        smtp_cfg.from_address = "bot@example.com"
        smtp_cfg.host = "smtp.example.com"
        smtp_cfg.port = 587
        smtp_cfg.user = ""
        smtp_cfg.password = ""
        smtp_cfg.use_tls = False
        mock_settings.return_value.smtp = smtp_cfg

        await _send_email(
            subject="Test",
            body="Body",
            recipients=["a@b.com", "c@d.com"],
        )

        mock_smtp.send.assert_called_once()


class TestGenerateAndSendWeeklyReport:
    """Test _generate_and_send_weekly_report."""

    @pytest.mark.asyncio
    @patch("src.tasks.email_report.get_settings")
    async def test_skips_when_no_recipients(self, mock_settings: MagicMock) -> None:
        smtp_cfg = MagicMock()
        smtp_cfg.recipient_list = []
        smtp_cfg.host = "smtp.example.com"
        mock_settings.return_value.smtp = smtp_cfg

        result = await _generate_and_send_weekly_report()
        assert result["status"] == "skipped"
        assert result["reason"] == "no_recipients"

    @pytest.mark.asyncio
    @patch("src.tasks.email_report.get_settings")
    async def test_skips_when_no_smtp_host(self, mock_settings: MagicMock) -> None:
        smtp_cfg = MagicMock()
        smtp_cfg.recipient_list = ["admin@example.com"]
        smtp_cfg.host = ""
        mock_settings.return_value.smtp = smtp_cfg

        result = await _generate_and_send_weekly_report()
        assert result["status"] == "skipped"
        assert result["reason"] == "no_smtp_host"

    @pytest.mark.asyncio
    @patch("src.tasks.email_report._send_email", new_callable=AsyncMock)
    @patch("src.tasks.email_report.generate_weekly_report", new_callable=AsyncMock)
    @patch("src.tasks.email_report.get_settings")
    async def test_generates_and_sends(
        self,
        mock_settings: MagicMock,
        mock_generate: AsyncMock,
        mock_send: AsyncMock,
    ) -> None:
        smtp_cfg = MagicMock()
        smtp_cfg.recipient_list = ["admin@example.com", "boss@example.com"]
        smtp_cfg.host = "smtp.example.com"
        mock_settings.return_value.smtp = smtp_cfg

        mock_generate.return_value = b"%PDF-1.4 report data"

        result = await _generate_and_send_weekly_report()
        assert result["status"] == "sent"
        assert result["recipients"] == 2

        mock_generate.assert_called_once()
        mock_send.assert_called_once()
        # Check the attachment was passed
        send_kwargs = mock_send.call_args
        assert send_kwargs.kwargs.get("attachment") == b"%PDF-1.4 report data"
