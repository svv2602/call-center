"""CLI commands for analytics and call inspection.

Usage:
    call-center-admin stats today
    call-center-admin stats recalculate --date YYYY-MM-DD
    call-center-admin calls list --date YYYY-MM-DD --limit 20
    call-center-admin calls show <call_id>
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings

stats_app = typer.Typer(help="Statistics commands")
calls_app = typer.Typer(help="Call inspection commands")


async def _get_engine() -> AsyncEngine:
    """Create async database engine from settings."""
    settings = get_settings()
    return create_async_engine(settings.database.url, pool_pre_ping=True)


async def _fetch_today_stats() -> dict[str, Any]:
    """Fetch today's stats from daily_stats or live calls table."""
    engine = await _get_engine()
    today = date.today().isoformat()

    try:
        async with engine.begin() as conn:
            # Try daily_stats first (pre-aggregated)
            result = await conn.execute(
                text("""
                    SELECT total_calls, resolved_by_bot, transferred,
                           avg_duration_seconds, avg_quality_score, total_cost_usd
                    FROM daily_stats
                    WHERE stat_date = :today
                """),
                {"today": today},
            )
            row = result.first()
            if row:
                return dict(row._mapping)

            # Fall back to live aggregation from calls table
            result = await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total_calls,
                        COUNT(*) FILTER (WHERE NOT transferred_to_operator) AS resolved_by_bot,
                        COUNT(*) FILTER (WHERE transferred_to_operator) AS transferred,
                        COALESCE(AVG(duration_seconds), 0) AS avg_duration_seconds,
                        COALESCE(AVG(quality_score), 0) AS avg_quality_score,
                        COALESCE(SUM(total_cost_usd), 0) AS total_cost_usd
                    FROM calls
                    WHERE started_at::date = :today
                """),
                {"today": today},
            )
            row = result.first()
            if row is None:
                msg = "Expected row from aggregate query"
                raise RuntimeError(msg)
            return dict(row._mapping)
    finally:
        await engine.dispose()


async def _fetch_calls_list(target_date: str | None, limit: int) -> list[dict[str, Any]]:
    """Fetch a list of calls with basic metadata."""
    engine = await _get_engine()

    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit}

    if target_date:
        conditions.append("started_at::date = :target_date")
        params["target_date"] = target_date

    where_clause = " AND ".join(conditions)

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(f"""
                    SELECT id, caller_id, started_at, duration_seconds,
                           scenario, transferred_to_operator, quality_score
                    FROM calls
                    WHERE {where_clause}
                    ORDER BY started_at DESC
                    LIMIT :limit
                """),
                params,
            )
            return [dict(row._mapping) for row in result]
    finally:
        await engine.dispose()


async def _fetch_call_details(call_id: str) -> dict[str, Any] | None:
    """Fetch full call details including turns and tool calls."""
    engine = await _get_engine()

    try:
        async with engine.begin() as conn:
            # Call metadata
            result = await conn.execute(
                text("""
                    SELECT id, caller_id, started_at, ended_at,
                           duration_seconds, scenario, transferred_to_operator,
                           quality_score, total_cost_usd
                    FROM calls WHERE id = :call_id
                """),
                {"call_id": call_id},
            )
            call_row = result.first()
            if not call_row:
                return None
            call_data = dict(call_row._mapping)

            # Turns
            turns_result = await conn.execute(
                text("""
                    SELECT turn_index, speaker, text
                    FROM call_turns
                    WHERE call_id = :call_id
                    ORDER BY turn_index
                """),
                {"call_id": call_id},
            )
            call_data["turns"] = [dict(r._mapping) for r in turns_result]

            # Tool calls
            tools_result = await conn.execute(
                text("""
                    SELECT tool_name, success, duration_ms
                    FROM call_tool_calls
                    WHERE call_id = :call_id
                    ORDER BY called_at
                """),
                {"call_id": call_id},
            )
            call_data["tool_calls"] = [dict(r._mapping) for r in tools_result]

            return call_data
    finally:
        await engine.dispose()


@stats_app.command("today")
def stats_today() -> None:
    """Show today's call statistics summary."""
    try:
        stats = asyncio.run(_fetch_today_stats())
    except Exception as e:
        typer.echo(typer.style(f"Failed to fetch stats: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    total = stats.get("total_calls", 0)
    resolved = stats.get("resolved_by_bot", 0)
    transferred = stats.get("transferred", 0)
    resolved_pct = (resolved / total * 100) if total > 0 else 0

    typer.echo(f"Date:         {date.today().isoformat()}")
    typer.echo(f"Total calls:  {total}")
    typer.echo(f"Resolved:     {resolved} ({resolved_pct:.1f}%)")
    typer.echo(f"Transferred:  {transferred}")
    typer.echo(f"Avg duration: {stats.get('avg_duration_seconds', 0):.0f}s")
    typer.echo(f"Avg quality:  {stats.get('avg_quality_score', 0):.2f}")
    typer.echo(f"Total cost:   ${stats.get('total_cost_usd', 0):.4f}")


@stats_app.command("recalculate")
def stats_recalculate(
    target_date: str = typer.Option(..., "--date", help="Date to recalculate (YYYY-MM-DD)"),
) -> None:
    """Recalculate daily stats for a specific date via Celery task."""
    from src.tasks.daily_stats import calculate_daily_stats

    typer.echo(f"Recalculating stats for {target_date}...")
    try:
        result = calculate_daily_stats.delay(target_date)
        typer.echo(f"Task submitted: {result.id}")
        typer.echo("Use 'celery -A src.tasks.celery_app inspect active' to check status.")
    except Exception as e:
        typer.echo(typer.style(f"Failed to submit task: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None


@calls_app.command("list")
def calls_list(
    target_date: str | None = typer.Option(None, "--date", help="Filter by date (YYYY-MM-DD)"),
    limit: int = typer.Option(20, "--limit", help="Number of calls to show"),
) -> None:
    """List recent calls with basic metadata."""
    try:
        calls = asyncio.run(_fetch_calls_list(target_date, limit))
    except Exception as e:
        typer.echo(typer.style(f"Failed to fetch calls: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    if not calls:
        typer.echo("No calls found.")
        return

    # Simple table output
    typer.echo(f"{'ID':<38} {'Caller':<16} {'Duration':<10} {'Quality':<9} {'Status'}")
    typer.echo("-" * 90)
    for call in calls:
        status = "transferred" if call.get("transferred_to_operator") else "resolved"
        quality = call.get("quality_score")
        quality_str = f"{quality:.2f}" if quality is not None else "N/A"
        call_id_str = str(call["id"])
        caller_str = str(call.get("caller_id", "N/A"))
        typer.echo(
            f"{call_id_str:<38} "
            f"{caller_str:<16} "
            f"{call.get('duration_seconds', 0):>6}s   "
            f"{quality_str:<9} "
            f"{status}"
        )
    typer.echo(f"\nShowing {len(calls)} call(s)")


@calls_app.command("show")
def calls_show(
    call_id: str = typer.Argument(..., help="Call ID (UUID)"),
) -> None:
    """Show detailed information about a specific call."""
    try:
        call = asyncio.run(_fetch_call_details(call_id))
    except Exception as e:
        typer.echo(typer.style(f"Failed to fetch call: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    if not call:
        typer.echo(typer.style(f"Call not found: {call_id}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    typer.echo(typer.style("=== Call Details ===", bold=True))
    typer.echo(f"ID:          {call['id']}")
    typer.echo(f"Caller:      {call.get('caller_id', 'N/A')}")
    typer.echo(f"Started:     {call.get('started_at', 'N/A')}")
    typer.echo(f"Ended:       {call.get('ended_at', 'N/A')}")
    typer.echo(f"Duration:    {call.get('duration_seconds', 0)}s")
    typer.echo(f"Scenario:    {call.get('scenario', 'N/A')}")
    typer.echo(f"Transferred: {call.get('transferred_to_operator', False)}")
    typer.echo(f"Quality:     {call.get('quality_score', 'N/A')}")
    typer.echo(f"Cost:        ${call.get('total_cost_usd', 0):.4f}")

    turns = call.get("turns", [])
    if turns:
        typer.echo(typer.style("\n=== Transcription ===", bold=True))
        for turn in turns:
            speaker = turn.get("speaker", "?")
            text_content = turn.get("text", "")
            prefix = "BOT:" if speaker == "bot" else "USER:"
            typer.echo(f"  {prefix} {text_content}")

    tool_calls = call.get("tool_calls", [])
    if tool_calls:
        typer.echo(typer.style("\n=== Tool Calls ===", bold=True))
        for tc in tool_calls:
            status = "\u2705" if tc.get("success") else "\u274c"
            typer.echo(f"  {status} {tc['tool_name']} ({tc.get('duration_ms', 0)}ms)")
