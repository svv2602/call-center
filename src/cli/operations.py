"""CLI commands for operational management.

Usage:
    call-center-admin ops celery-status
    call-center-admin ops celery-purge <queue>
    call-center-admin ops config-reload
    call-center-admin ops system-status
"""

from __future__ import annotations

import typer

ops_app = typer.Typer(help="Operational management commands")


@ops_app.command("celery-status")
def celery_status() -> None:
    """Show Celery worker status and queue info."""
    try:
        from src.tasks.celery_app import app as celery_app

        inspect = celery_app.control.inspect(timeout=5)

        typer.echo(typer.style("Celery Workers", bold=True))
        typer.echo("-" * 40)

        ping_result = inspect.ping()
        if not ping_result:
            typer.echo(typer.style("No workers responding", fg=typer.colors.RED))
            raise typer.Exit(code=1)

        for worker_name in ping_result:
            typer.echo(typer.style(f"  \u2705 {worker_name}", fg=typer.colors.GREEN))

        active = inspect.active()
        if active:
            typer.echo(typer.style("\nActive Tasks", bold=True))
            typer.echo("-" * 40)
            for worker_name, tasks in active.items():
                if tasks:
                    for task in tasks:
                        typer.echo(f"  [{worker_name}] {task['name']} (id={task['id'][:8]}...)")
                else:
                    typer.echo(f"  [{worker_name}] idle")

        scheduled = inspect.scheduled()
        if scheduled:
            total_scheduled = sum(len(tasks) for tasks in scheduled.values())
            typer.echo(f"\nScheduled tasks: {total_scheduled}")

    except Exception as e:
        typer.echo(typer.style(f"Failed to connect to Celery: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None


@ops_app.command("celery-purge")
def celery_purge(
    queue: str = typer.Argument(..., help="Queue name to purge (quality, stats)"),
) -> None:
    """Purge all pending tasks from a Celery queue."""
    typer.confirm(f"This will delete ALL pending tasks from queue '{queue}'. Continue?", abort=True)

    try:
        from src.tasks.celery_app import app as celery_app

        purged = celery_app.control.purge()
        typer.echo(
            typer.style(f"\u2705 Purged {purged} tasks from '{queue}'", fg=typer.colors.GREEN)
        )
    except Exception as e:
        typer.echo(typer.style(f"Purge failed: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None


@ops_app.command("config-reload")
def config_reload() -> None:
    """Reload safe configuration parameters from environment."""
    from src.config import FeatureFlagSettings, LoggingSettings, QualitySettings, get_settings

    settings = get_settings()

    old = {
        "quality.llm_model": settings.quality.llm_model,
        "feature_flags.stt_provider": settings.feature_flags.stt_provider,
        "logging.level": settings.logging.level,
    }

    settings.quality = QualitySettings()
    settings.feature_flags = FeatureFlagSettings()
    settings.logging = LoggingSettings()

    typer.echo(typer.style("Configuration reloaded:", bold=True))
    for key, old_val in old.items():
        new_val = key.split(".")
        section = getattr(settings, new_val[0])
        new = getattr(section, new_val[1])
        changed = " (changed)" if str(old_val) != str(new) else ""
        typer.echo(f"  {key}: {old_val} -> {new}{changed}")

    typer.echo(typer.style("\u2705 Done", fg=typer.colors.GREEN))


@ops_app.command("system-status")
def system_status_cmd() -> None:
    """Show full system status."""
    import time
    from pathlib import Path

    from src.config import get_settings

    settings = get_settings()
    typer.echo(typer.style("System Status", bold=True))
    typer.echo("-" * 40)
    typer.echo("  Version: 0.1.0")
    db_host = settings.database.url.split("@")[-1] if "@" in settings.database.url else "configured"
    typer.echo(f"  Database: {db_host}")
    typer.echo(f"  Redis: {settings.redis.url}")
    typer.echo(f"  Backup dir: {settings.backup.backup_dir}")

    # Check Celery
    try:
        from src.tasks.celery_app import app as celery_app

        inspect = celery_app.control.inspect(timeout=3)
        ping_result = inspect.ping()
        workers = len(ping_result) if ping_result else 0
        typer.echo(f"  Celery workers: {workers}")
    except Exception:
        typer.echo("  Celery workers: unavailable")

    # Check latest backup
    backup_dir = Path(settings.backup.backup_dir)
    if backup_dir.exists():
        backups = sorted(
            backup_dir.glob("callcenter_*.sql*"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        if backups:
            latest = backups[0]
            size_mb = latest.stat().st_size / (1024 * 1024)
            mtime = time.strftime("%Y-%m-%d %H:%M", time.gmtime(latest.stat().st_mtime))
            typer.echo(f"  Last backup: {latest.name} ({size_mb:.1f} MB, {mtime})")
        else:
            typer.echo("  Last backup: none")
    else:
        typer.echo(f"  Backup dir: not found ({backup_dir})")
