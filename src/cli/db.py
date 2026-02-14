"""CLI commands for database management.

Usage:
    call-center-admin db backup [--compress]
    call-center-admin db restore <file>
    call-center-admin db migrations status
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import typer

from src.config import get_settings

db_app = typer.Typer(help="Database management commands")


def _parse_database_url(url: str) -> dict[str, str]:
    """Parse a database URL into connection components."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/"),
    }


def _build_pg_dump_cmd(db_params: dict[str, str], output_path: str) -> list[str]:
    """Build a pg_dump command from database parameters."""
    cmd = ["pg_dump", "--no-password", "-h", db_params["host"], "-p", db_params["port"]]
    if db_params["user"]:
        cmd.extend(["-U", db_params["user"]])
    cmd.extend(["-f", output_path, db_params["dbname"]])
    return cmd


def _build_pg_dump_env(db_params: dict[str, str]) -> dict[str, str]:
    """Build environment dict with PGPASSWORD for pg_dump."""
    import os

    env = os.environ.copy()
    if db_params["password"]:
        env["PGPASSWORD"] = db_params["password"]
    return env


@db_app.command("backup")
def backup(
    compress: bool = typer.Option(False, "--compress", help="Compress backup with gzip"),
    output_dir: str | None = typer.Option(None, "--output-dir", help="Output directory"),
) -> None:
    """Create a PostgreSQL database backup using pg_dump."""
    settings = get_settings()
    db_params = _parse_database_url(settings.database.url)
    backup_dir = Path(output_dir) if output_dir else Path(settings.backup.backup_dir)

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    filename = f"callcenter_{timestamp}.sql"
    output_path = backup_dir / filename

    typer.echo(f"Backing up database '{db_params['dbname']}' to {output_path}...")

    cmd = _build_pg_dump_cmd(db_params, str(output_path))
    env = _build_pg_dump_env(db_params)

    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        typer.echo(
            typer.style("pg_dump not found. Install PostgreSQL client tools.", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1) from None
    except subprocess.CalledProcessError as e:
        typer.echo(typer.style(f"pg_dump failed: {e.stderr.strip()}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    if compress:
        gz_path = output_path.with_suffix(".sql.gz")
        with open(output_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        output_path.unlink()
        output_path = gz_path

    size_mb = output_path.stat().st_size / (1024 * 1024)
    typer.echo(
        typer.style(
            f"\u2705 Backup created: {output_path} ({size_mb:.2f} MB)", fg=typer.colors.GREEN
        )
    )


@db_app.command("restore")
def restore(
    file: str = typer.Argument(..., help="Path to SQL dump file"),
) -> None:
    """Restore a PostgreSQL database from a backup file."""
    backup_file = Path(file)
    if not backup_file.is_file():
        typer.echo(typer.style(f"File not found: {file}", fg=typer.colors.RED))
        raise typer.Exit(code=1)

    settings = get_settings()
    db_params = _parse_database_url(settings.database.url)

    typer.confirm(
        f"This will restore database '{db_params['dbname']}' from {file}. Continue?",
        abort=True,
    )

    # Decompress if gzipped
    restore_path = str(backup_file)
    temp_file: Path | None = None
    if backup_file.suffix == ".gz":
        temp_file = backup_file.with_suffix("")
        with gzip.open(backup_file, "rb") as f_in, open(temp_file, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        restore_path = str(temp_file)

    cmd = ["psql", "--no-password", "-h", db_params["host"], "-p", db_params["port"]]
    if db_params["user"]:
        cmd.extend(["-U", db_params["user"]])
    cmd.extend(["-d", db_params["dbname"], "-f", restore_path])

    env = _build_pg_dump_env(db_params)

    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
        typer.echo(typer.style("\u2705 Database restored successfully", fg=typer.colors.GREEN))
    except FileNotFoundError:
        typer.echo(
            typer.style("psql not found. Install PostgreSQL client tools.", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1) from None
    except subprocess.CalledProcessError as e:
        typer.echo(typer.style(f"Restore failed: {e.stderr.strip()}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
    finally:
        if temp_file and temp_file.is_file():
            temp_file.unlink()


@db_app.command("migrations-status")
def migrations_status() -> None:
    """Show current Alembic migration revision."""
    try:
        result = subprocess.run(
            ["alembic", "current"],
            capture_output=True,
            text=True,
            check=True,
        )
        typer.echo(result.stdout.strip() or "No migrations applied.")
    except FileNotFoundError:
        typer.echo(
            typer.style("alembic not found. Install: pip install alembic", fg=typer.colors.RED)
        )
        raise typer.Exit(code=1) from None
    except subprocess.CalledProcessError as e:
        typer.echo(typer.style(f"Alembic error: {e.stderr.strip()}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
