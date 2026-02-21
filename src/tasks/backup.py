"""Automated backup tasks for all system components.

Runs via Celery Beat:
- PostgreSQL: daily at 04:00 (pg_dump + gzip + rotation)
- Redis: daily at 04:15 (RDB snapshot copy + gzip)
- Knowledge base: weekly Sunday at 01:00 (tar.gz archive)
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

import redis as sync_redis

from src.config import get_settings
from src.monitoring.metrics import (
    backup_duration_seconds,
    backup_errors_total,
    backup_last_size_bytes,
    backup_last_success_timestamp,
)
from src.tasks.celery_app import app

logger = logging.getLogger(__name__)


def _acquire_lock(lock_name: str, ttl: int) -> sync_redis.Redis | None:
    """Try to acquire a Redis distributed lock. Returns client if acquired, None if not."""
    try:
        settings = get_settings()
        r = sync_redis.from_url(settings.redis.url)
        if r.set(f"backup:{lock_name}:lock", "1", nx=True, ex=ttl):
            return r
        logger.info("Backup '%s' already running, skipping", lock_name)
        r.close()
        return None
    except Exception:
        # Redis unavailable — proceed without lock (best effort)
        logger.debug("Redis unavailable for backup lock, proceeding without lock")
        return None


def _release_lock(r: sync_redis.Redis | None, lock_name: str) -> None:
    """Release a Redis distributed lock."""
    if r is not None:
        try:
            r.delete(f"backup:{lock_name}:lock")
            r.close()
        except Exception:
            pass


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

    subprocess.run(cmd, env=env, check=True, capture_output=True, text=True, timeout=1800)


def _compress_file(source: Path) -> Path:
    """Compress a file with gzip and remove the original."""
    gz_path = source.with_suffix(source.suffix + ".gz")
    with open(source, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    source.unlink()
    return gz_path


def _rotate_backups(backup_dir: Path, pattern: str, retention_days: int) -> int:
    """Delete backups older than retention_days. Returns count of deleted files."""
    now = datetime.now(UTC)
    deleted = 0
    for f in backup_dir.glob(pattern):
        age_days = (now.timestamp() - f.stat().st_mtime) / 86400
        if age_days > retention_days:
            f.unlink()
            deleted += 1
            logger.info("Deleted old backup: %s (%.1f days old)", f.name, age_days)
    return deleted


@app.task(name="src.tasks.backup.backup_database", soft_time_limit=1800, time_limit=1860)  # type: ignore[untyped-decorator]
def backup_database() -> dict[str, Any]:
    """Create a compressed PostgreSQL backup with rotation.

    Called by Celery Beat daily at 04:00 Kyiv time.
    """
    lock = _acquire_lock("database", ttl=1860)
    if lock is None:
        return {"status": "skipped", "reason": "already_running"}

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

        # Update Prometheus metrics
        backup_last_success_timestamp.labels(component="postgres").set(time.time())
        backup_last_size_bytes.labels(component="postgres").set(size_bytes)
        backup_duration_seconds.labels(component="postgres").observe(duration_s)

        # Rotate old backups
        deleted = _rotate_backups(backup_dir, "callcenter_*.sql*", settings.backup.retention_days)

        return {
            "status": "success",
            "file": str(gz_path),
            "size_bytes": size_bytes,
            "duration_seconds": round(duration_s, 2),
            "rotated_count": deleted,
        }

    except FileNotFoundError:
        logger.error("pg_dump not found — install PostgreSQL client tools")
        backup_errors_total.labels(component="postgres").inc()
        return {"status": "error", "error": "pg_dump not found"}
    except subprocess.CalledProcessError as e:
        logger.error("pg_dump failed: %s", e.stderr.strip() if e.stderr else str(e))
        backup_errors_total.labels(component="postgres").inc()
        return {"status": "error", "error": str(e)}
    finally:
        _release_lock(lock, "database")


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


@app.task(name="src.tasks.backup.verify_latest_backup", soft_time_limit=120, time_limit=150)  # type: ignore[untyped-decorator]
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


@app.task(name="src.tasks.backup.backup_redis", soft_time_limit=600, time_limit=660)  # type: ignore[untyped-decorator]
def backup_redis() -> dict[str, Any]:
    """Create a Redis RDB snapshot backup with rotation.

    Called by Celery Beat daily at 04:15 Kyiv time.
    Uses redis-cli to trigger BGSAVE and copies the dump.rdb file.
    """
    lock = _acquire_lock("redis", ttl=660)
    if lock is None:
        return {"status": "skipped", "reason": "already_running"}

    start = time.monotonic()
    settings = get_settings()
    redis_url = settings.redis.url

    backup_dir = Path(settings.backup.backup_dir) / "redis"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")

    try:
        # Parse Redis URL for host/port
        from urllib.parse import urlparse as _urlparse

        parsed = _urlparse(redis_url)
        redis_host = parsed.hostname or "localhost"
        redis_port = str(parsed.port or 6379)

        # Trigger BGSAVE
        subprocess.run(
            ["redis-cli", "-h", redis_host, "-p", redis_port, "BGSAVE"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Wait briefly for BGSAVE to complete
        import time as _time

        _time.sleep(2)

        # Get Redis data directory and copy dump.rdb
        result_info = subprocess.run(
            ["redis-cli", "-h", redis_host, "-p", redis_port, "CONFIG", "GET", "dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        redis_dir_lines = result_info.stdout.strip().split("\n")
        redis_dir = redis_dir_lines[1] if len(redis_dir_lines) > 1 else "/data"

        rdb_source = Path(redis_dir) / "dump.rdb"
        rdb_dest = backup_dir / f"redis_{timestamp}.rdb"

        if rdb_source.exists():
            shutil.copy2(rdb_source, rdb_dest)
        else:
            # Fallback: try docker-accessible path
            rdb_dest_path = backup_dir / f"redis_{timestamp}.rdb"
            subprocess.run(
                ["cp", str(rdb_source), str(rdb_dest_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )

        gz_path = _compress_file(rdb_dest)
        size_bytes = gz_path.stat().st_size
        duration_s = time.monotonic() - start

        backup_last_success_timestamp.labels(component="redis").set(time.time())
        backup_last_size_bytes.labels(component="redis").set(size_bytes)
        backup_duration_seconds.labels(component="redis").observe(duration_s)

        _rotate_backups(backup_dir, "redis_*.rdb*", 7)

        logger.info("Redis backup created: %s (%d bytes)", gz_path, size_bytes)
        return {"status": "success", "file": str(gz_path), "size_bytes": size_bytes}

    except Exception as e:
        backup_errors_total.labels(component="redis").inc()
        logger.error("Redis backup failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        _release_lock(lock, "redis")


@app.task(name="src.tasks.backup.backup_knowledge_base", soft_time_limit=600, time_limit=660)  # type: ignore[untyped-decorator]
def backup_knowledge_base() -> dict[str, Any]:
    """Create a tar.gz archive of the knowledge_base directory.

    Called by Celery Beat weekly on Sunday at 01:00 Kyiv time.
    """
    lock = _acquire_lock("knowledge", ttl=660)
    if lock is None:
        return {"status": "skipped", "reason": "already_running"}

    start = time.monotonic()
    settings = get_settings()

    backup_dir = Path(settings.backup.backup_dir) / "knowledge"
    backup_dir.mkdir(parents=True, exist_ok=True)

    knowledge_dir = Path("knowledge_base")
    if not knowledge_dir.exists():
        logger.warning("Knowledge base directory not found, skipping backup")
        return {"status": "skipped", "reason": "knowledge_base directory not found"}

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    archive_path = backup_dir / f"knowledge_{timestamp}.tar.gz"

    try:
        import tarfile

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(knowledge_dir, arcname="knowledge_base")

        size_bytes = archive_path.stat().st_size
        duration_s = time.monotonic() - start

        backup_last_success_timestamp.labels(component="knowledge").set(time.time())
        backup_last_size_bytes.labels(component="knowledge").set(size_bytes)
        backup_duration_seconds.labels(component="knowledge").observe(duration_s)

        _rotate_backups(backup_dir, "knowledge_*.tar.gz", 30)

        logger.info("Knowledge base backup created: %s (%d bytes)", archive_path, size_bytes)
        return {"status": "success", "file": str(archive_path), "size_bytes": size_bytes}

    except Exception as e:
        backup_errors_total.labels(component="knowledge").inc()
        logger.error("Knowledge base backup failed: %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        _release_lock(lock, "knowledge")
