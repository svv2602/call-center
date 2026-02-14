"""Automated PostgreSQL backup task.

Runs daily via Celery Beat to create compressed pg_dump backups
with automatic rotation of old backups.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.config import get_settings
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


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


def _run_pg_dump(db_params: dict[str, str], output_path: Path) -> None:
    """Execute pg_dump and write SQL to output_path."""
    import os

    cmd = [
        "pg_dump",
        "--no-password",
        "-h",
        db_params["host"],
        "-p",
        db_params["port"],
    ]
    if db_params["user"]:
        cmd.extend(["-U", db_params["user"]])
    cmd.extend(["-f", str(output_path), db_params["dbname"]])

    env = os.environ.copy()
    if db_params["password"]:
        env["PGPASSWORD"] = db_params["password"]

    subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)


def _compress_file(source: Path) -> Path:
    """Compress a file with gzip and remove the original."""
    gz_path = source.with_suffix(source.suffix + ".gz")
    with open(source, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    source.unlink()
    return gz_path


def _rotate_backups(backup_dir: Path, retention_days: int) -> int:
    """Delete backups older than retention_days. Returns count of deleted files."""
    now = datetime.now(UTC)
    deleted = 0
    for f in backup_dir.glob("callcenter_*.sql*"):
        age_days = (now.timestamp() - f.stat().st_mtime) / 86400
        if age_days > retention_days:
            f.unlink()
            deleted += 1
            logger.info("Deleted old backup: %s (%.1f days old)", f.name, age_days)
    return deleted


@app.task(name="src.tasks.backup.backup_database")  # type: ignore[untyped-decorator]
def backup_database() -> dict[str, Any]:
    """Create a compressed PostgreSQL backup with rotation.

    Called by Celery Beat daily at 04:00 Kyiv time.
    """
    start = time.monotonic()
    settings = get_settings()
    db_params = _parse_database_url(settings.database.url)

    backup_dir = Path(settings.backup.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    sql_path = backup_dir / f"callcenter_{timestamp}.sql"

    try:
        _run_pg_dump(db_params, sql_path)
        gz_path = _compress_file(sql_path)
        size_bytes = gz_path.stat().st_size
        duration_s = time.monotonic() - start

        logger.info(
            "Backup created",
            extra={
                "event": "backup_created",
                "file": str(gz_path),
                "size_bytes": size_bytes,
                "duration_seconds": round(duration_s, 2),
            },
        )

        # Rotate old backups
        deleted = _rotate_backups(backup_dir, settings.backup.retention_days)

        return {
            "status": "success",
            "file": str(gz_path),
            "size_bytes": size_bytes,
            "duration_seconds": round(duration_s, 2),
            "rotated_count": deleted,
        }

    except FileNotFoundError:
        logger.error("pg_dump not found — install PostgreSQL client tools")
        return {"status": "error", "error": "pg_dump not found"}
    except subprocess.CalledProcessError as e:
        logger.error("pg_dump failed: %s", e.stderr.strip() if e.stderr else str(e))
        return {"status": "error", "error": str(e)}


def verify_backup(filepath: str | Path) -> dict[str, Any]:
    """Verify backup integrity by decompressing and checking SQL content.

    For .sql.gz files: gunzip to temp, then check content.
    For .sql files: check content directly.
    Returns dict with status and details.
    """
    path = Path(filepath)
    if not path.is_file():
        return {"status": "error", "error": f"File not found: {filepath}"}

    try:
        if path.suffix == ".gz":
            # Verify gzip integrity and check first bytes
            with gzip.open(path, "rb") as f:
                header = f.read(1024)
                # Count approximate lines by reading in chunks
                size = len(header)
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
        else:
            with open(path, "rb") as f:
                header = f.read(1024)
                size = path.stat().st_size

        # Basic SQL dump validation: should start with typical pg_dump output
        header_text = header.decode("utf-8", errors="replace")
        is_valid_sql = any(
            marker in header_text
            for marker in ("-- PostgreSQL", "SET statement_timeout", "CREATE", "pg_dump")
        )

        if not is_valid_sql:
            return {
                "status": "error",
                "error": "File does not appear to be a valid PostgreSQL dump",
                "file": str(path),
            }

        return {
            "status": "ok",
            "file": str(path),
            "size_bytes": path.stat().st_size,
            "uncompressed_bytes": size,
            "compressed": path.suffix == ".gz",
        }

    except gzip.BadGzipFile:
        return {"status": "error", "error": "Corrupt gzip file", "file": str(path)}
    except Exception as e:
        return {"status": "error", "error": str(e), "file": str(path)}


@app.task(name="src.tasks.backup.verify_latest_backup")  # type: ignore[untyped-decorator]
def verify_latest_backup() -> dict[str, Any]:
    """Verify the most recent backup file.

    Called after each backup to ensure integrity.
    """
    settings = get_settings()
    backup_dir = Path(settings.backup.backup_dir)

    if not backup_dir.exists():
        return {"status": "error", "error": f"Backup directory not found: {backup_dir}"}

    backups = sorted(
        backup_dir.glob("callcenter_*.sql*"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        return {"status": "error", "error": "No backups found"}

    latest = backups[0]
    result = verify_backup(latest)

    if result["status"] != "ok":
        logger.error("Backup verification FAILED: %s", result)
    else:
        logger.info("Backup verified: %s (%d bytes)", latest.name, result["size_bytes"])

    return result
