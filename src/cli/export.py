"""CLI commands for data export.

Usage:
    call-center-admin export calls --date-from 2026-02-01 --date-to 2026-02-14 --output calls.csv
    call-center-admin export report --date-from 2026-02-01 --date-to 2026-02-14 --output report.pdf
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from typing import Any

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.logging.pii_sanitizer import sanitize_phone

logger = logging.getLogger(__name__)
export_app = typer.Typer(help="Data export commands")

_MAX_EXPORT_ROWS = 10000


async def _get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database.url)


async def _export_calls_csv(
    date_from: str | None,
    date_to: str | None,
    output: str,
) -> int:
    """Export calls to CSV file. Returns row count."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": _MAX_EXPORT_ROWS}

    if date_from:
        conditions.append("started_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("started_at < :date_to::date + interval '1 day'")
        params["date_to"] = date_to

    where_clause = " AND ".join(conditions)

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(f"""
                    SELECT
                        id, started_at, duration_seconds, caller_id,
                        scenario, transferred_to_operator, transfer_reason,
                        quality_score, total_cost_usd
                    FROM calls
                    WHERE {where_clause}
                    ORDER BY started_at DESC
                    LIMIT :limit
                """),
                params,
            )
            rows = [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()

    columns = [
        "call_id", "started_at", "duration_sec", "caller_id",
        "scenario", "transferred", "transfer_reason",
        "quality_score", "total_cost",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "call_id": str(r["id"]),
            "started_at": str(r["started_at"] or ""),
            "duration_sec": r["duration_seconds"] or 0,
            "caller_id": sanitize_phone(str(r["caller_id"] or "")),
            "scenario": r["scenario"] or "",
            "transferred": r["transferred_to_operator"] or False,
            "transfer_reason": r["transfer_reason"] or "",
            "quality_score": r["quality_score"] if r["quality_score"] is not None else "",
            "total_cost": r["total_cost_usd"] if r["total_cost_usd"] is not None else "",
        })

    with open(output, "w") as f:
        f.write(buf.getvalue())

    return len(rows)


async def _export_report_pdf(date_from: str, date_to: str, output: str) -> None:
    """Generate PDF report and save to file."""
    from src.reports.generator import generate_weekly_report

    pdf_bytes = await generate_weekly_report(date_from, date_to)
    with open(output, "wb") as f:
        f.write(pdf_bytes)


@export_app.command("calls")
def export_calls(
    date_from: str | None = typer.Option(None, "--date-from", help="Start date (YYYY-MM-DD)"),
    date_to: str | None = typer.Option(None, "--date-to", help="End date (YYYY-MM-DD)"),
    output: str = typer.Option("calls.csv", "--output", "-o", help="Output file path"),
) -> None:
    """Export calls to CSV file."""
    typer.echo(f"Exporting calls to {output}...")
    try:
        count = asyncio.run(_export_calls_csv(date_from, date_to, output))
        typer.echo(typer.style(f"Exported {count} calls to {output}", fg=typer.colors.GREEN))
    except Exception as e:
        typer.echo(typer.style(f"Export failed: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None


@export_app.command("report")
def export_report(
    date_from: str = typer.Option(..., "--date-from", help="Start date (YYYY-MM-DD)"),
    date_to: str = typer.Option(..., "--date-to", help="End date (YYYY-MM-DD)"),
    output: str = typer.Option("report.pdf", "--output", "-o", help="Output file path"),
) -> None:
    """Generate PDF report and save to file."""
    typer.echo(f"Generating report for {date_from} — {date_to}...")
    try:
        asyncio.run(_export_report_pdf(date_from, date_to, output))
        typer.echo(typer.style(f"Report saved to {output}", fg=typer.colors.GREEN))
    except Exception as e:
        typer.echo(typer.style(f"Report generation failed: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
