"""Unit tests for automated PostgreSQL backup task."""

from __future__ import annotations

import gzip
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.tasks.backup import (
    _compress_file,
    _parse_database_url,
    _rotate_backups,
    _run_pg_dump,
)


class TestParseDatabaseUrl:
    """Test database URL parsing."""

    def test_full_url(self) -> None:
        params = _parse_database_url("postgresql+asyncpg://user:pass@db:5433/mydb")
        assert params["host"] == "db"
        assert params["port"] == "5433"
        assert params["user"] == "user"
        assert params["password"] == "pass"
        assert params["dbname"] == "mydb"

    def test_minimal_url(self) -> None:
        params = _parse_database_url("postgresql://localhost/test")
        assert params["host"] == "localhost"
        assert params["port"] == "5432"
        assert params["user"] == ""
        assert params["dbname"] == "test"


class TestCompressFile:
    """Test file compression."""

    def test_compresses_and_removes_original(self, tmp_path: Path) -> None:
        source = tmp_path / "test.sql"
        source.write_text("CREATE TABLE test;")

        gz_path = _compress_file(source)

        assert gz_path.suffix == ".gz"
        assert gz_path.exists()
        assert not source.exists()

        # Verify content is valid gzip
        with gzip.open(gz_path, "rt") as f:
            assert f.read() == "CREATE TABLE test;"


class TestRotateBackups:
    """Test backup rotation."""

    def test_deletes_old_backups(self, tmp_path: Path) -> None:
        # Create "old" file with mtime 10 days ago
        old_file = tmp_path / "callcenter_2026-02-01_040000.sql.gz"
        old_file.write_text("old")
        import os

        old_time = time.time() - (10 * 86400)
        os.utime(old_file, (old_time, old_time))

        # Create "recent" file
        new_file = tmp_path / "callcenter_2026-02-13_040000.sql.gz"
        new_file.write_text("new")

        deleted = _rotate_backups(tmp_path, "callcenter_*.sql*", retention_days=7)

        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_keeps_all_within_retention(self, tmp_path: Path) -> None:
        f = tmp_path / "callcenter_2026-02-14_040000.sql.gz"
        f.write_text("recent")

        deleted = _rotate_backups(tmp_path, "callcenter_*.sql*", retention_days=7)
        assert deleted == 0
        assert f.exists()

    def test_pattern_filtering(self, tmp_path: Path) -> None:
        """Rotate only matches the given glob pattern."""
        import os

        old_time = time.time() - (10 * 86400)
        # Redis backup (should not be matched by callcenter pattern)
        redis_file = tmp_path / "redis_2026-02-01.rdb.gz"
        redis_file.write_text("redis")
        os.utime(redis_file, (old_time, old_time))

        pg_file = tmp_path / "callcenter_2026-02-01_040000.sql.gz"
        pg_file.write_text("pg")
        os.utime(pg_file, (old_time, old_time))

        deleted = _rotate_backups(tmp_path, "callcenter_*.sql*", retention_days=7)
        assert deleted == 1
        assert redis_file.exists()  # Not matched by pattern


class TestRunPgDump:
    """Test pg_dump execution."""

    @patch("src.tasks.backup.subprocess.run")
    def test_builds_correct_command(self, mock_run: MagicMock) -> None:
        params = {
            "host": "localhost",
            "port": "5432",
            "user": "admin",
            "password": "secret",
            "dbname": "callcenter",
        }
        output = Path("/tmp/test.sql")
        _run_pg_dump(params, output)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "pg_dump"
        assert "-h" in cmd
        assert "localhost" in cmd
        assert "-U" in cmd
        assert "admin" in cmd
        assert "-f" in cmd
        assert str(output) in cmd

        # Check PGPASSWORD is set
        env = mock_run.call_args[1]["env"]
        assert env["PGPASSWORD"] == "secret"


class TestBackupTask:
    """Test the full backup_database Celery task."""

    @patch("src.tasks.backup._run_pg_dump")
    def test_successful_backup(self, mock_dump: MagicMock, tmp_path: Path) -> None:
        from src.config import BackupSettings, Settings

        settings = Settings(backup=BackupSettings(backup_dir=str(tmp_path), retention_days=7))

        def fake_dump(params: dict[str, str], output: Path) -> None:
            output.write_text("-- pg_dump output")

        mock_dump.side_effect = fake_dump

        with patch("src.tasks.backup.get_settings", return_value=settings):
            from src.tasks.backup import backup_database

            result = backup_database()

        assert result["status"] == "success"
        assert result["size_bytes"] > 0
        assert result["duration_seconds"] >= 0

    @patch("src.tasks.backup._run_pg_dump", side_effect=FileNotFoundError)
    def test_pg_dump_missing(self, mock_dump: MagicMock, tmp_path: Path) -> None:
        from src.config import BackupSettings, Settings

        settings = Settings(backup=BackupSettings(backup_dir=str(tmp_path)))

        with patch("src.tasks.backup.get_settings", return_value=settings):
            from src.tasks.backup import backup_database

            result = backup_database()

        assert result["status"] == "error"
        assert "pg_dump" in result["error"]


class TestBackupKnowledgeBase:
    """Test the knowledge base backup task."""

    def test_successful_backup(self, tmp_path: Path) -> None:
        from src.config import BackupSettings, Settings

        # Create a fake knowledge_base directory
        kb_dir = tmp_path / "knowledge_base"
        kb_dir.mkdir()
        (kb_dir / "article.md").write_text("# Test Article")

        settings = Settings(
            backup=BackupSettings(backup_dir=str(tmp_path / "backups"), retention_days=7)
        )

        with (
            patch("src.tasks.backup.get_settings", return_value=settings),
            patch("src.tasks.backup.Path") as _mock_path_cls,
        ):
            # We need knowledge_base dir to exist when checked
            import os

            old_cwd = os.getcwd()
            os.chdir(tmp_path)
            try:
                # Re-import to use real Path
                from importlib import reload

                import src.tasks.backup as backup_mod

                reload(backup_mod)

                with (
                    patch.object(backup_mod, "get_settings", return_value=settings),
                    patch.object(backup_mod, "_acquire_lock", return_value=MagicMock()),
                    patch.object(backup_mod, "_release_lock"),
                ):
                    result = backup_mod.backup_knowledge_base()
            finally:
                os.chdir(old_cwd)

        assert result["status"] == "success"
        assert result["size_bytes"] > 0

    def test_missing_knowledge_dir(self, tmp_path: Path) -> None:
        from src.config import BackupSettings, Settings

        settings = Settings(
            backup=BackupSettings(backup_dir=str(tmp_path / "backups"), retention_days=7)
        )

        import os

        old_cwd = os.getcwd()
        os.chdir(tmp_path)  # No knowledge_base dir here
        try:
            from importlib import reload

            import src.tasks.backup as backup_mod

            reload(backup_mod)

            with patch.object(backup_mod, "get_settings", return_value=settings):
                result = backup_mod.backup_knowledge_base()
        finally:
            os.chdir(old_cwd)

        assert result["status"] == "skipped"
