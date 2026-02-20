"""Weekly email report task.

Generates a PDF report for the previous week and sends it
to configured recipients via SMTP.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

import aiosmtplib

from src.config import get_settings
from src.reports.generator import generate_weekly_report
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


async def _send_email(
    subject: str,
    body: str,
    recipients: list[str],
    attachment: bytes | None = None,
    attachment_filename: str = "report.pdf",
) -> None:
    """Send an email with optional PDF attachment via aiosmtplib."""
    settings = get_settings()
    smtp = settings.smtp

    msg = EmailMessage()
    msg["From"] = smtp.from_address
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment is not None:
        msg.add_attachment(
            attachment,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename,
        )

    await aiosmtplib.send(
        msg,
        hostname=smtp.host,
        port=smtp.port,
        username=smtp.user or None,
        password=smtp.password or None,
        use_tls=smtp.use_tls,
    )


async def _generate_and_send_weekly_report() -> dict[str, Any]:
    """Generate PDF for last week and email it."""
    settings = get_settings()
    recipients = settings.smtp.recipient_list

    if not recipients:
        logger.warning("No SMTP_REPORT_RECIPIENTS configured, skipping email report")
        return {"status": "skipped", "reason": "no_recipients"}

    if not settings.smtp.host:
        logger.warning("SMTP_HOST not configured, skipping email report")
        return {"status": "skipped", "reason": "no_smtp_host"}

    # Previous week: Monday to Sunday
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)

    date_from = last_monday.isoformat()
    date_to = last_sunday.isoformat()

    logger.info("Generating weekly report: %s to %s", date_from, date_to)

    pdf_bytes = await generate_weekly_report(date_from, date_to)
    filename = f"report_{date_from}_{date_to}.pdf"

    await _send_email(
        subject=f"Call Center AI — Еженедельный отчёт ({date_from} — {date_to})",
        body=(
            f"Еженедельный отчёт Call Center AI за период {date_from} — {date_to}.\n\n"
            "PDF-отчёт во вложении."
        ),
        recipients=recipients,
        attachment=pdf_bytes,
        attachment_filename=filename,
    )

    logger.info("Weekly report sent to %d recipients", len(recipients))
    return {
        "status": "sent",
        "date_from": date_from,
        "date_to": date_to,
        "recipients": len(recipients),
    }


@app.task(name="src.tasks.email_report.send_weekly_report")  # type: ignore[untyped-decorator]
def send_weekly_report() -> dict[str, Any]:
    """Celery task: generate and send weekly report."""
    return asyncio.run(_generate_and_send_weekly_report())
