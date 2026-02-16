"""Unit tests for CLI db commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestParseDbUrl:
    """Test database URL parsing."""

    def test_parse_full_url(self) -> None:
        from src.cli.db import _parse_database_url

        params = _parse_database_url("postgresql+asyncpg://user:pass@host:5433/mydb")
        assert params["host"] == "host"
        assert params["port"] == "5433"
        assert params["user"] == "user"
        assert params["password"] == "pass"
        assert params["dbname"] == "mydb"

    def test_parse_minimal_url(self) -> None:
        from src.cli.db import _parse_database_url

        params = _parse_database_url("postgresql://localhost/testdb")
        assert params["host"] == "localhost"
        assert params["port"] == "5432"
        assert params["dbname"] == "testdb"


class TestBuildPgDumpCmd:
    """Test pg_dump command building."""

    def test_builds_correct_command(self) -> None:
        from src.cli.db import _build_pg_dump_cmd

        params = {
            "host": "db-host",
            "port": "5432",
            "user": "admin",
            "password": "secret",
            "dbname": "callcenter",
        }
        cmd = _build_pg_dump_cmd(params, "/tmp/backup.sql")
        assert cmd[0] == "pg_dump"
        assert "--no-password" in cmd
        assert "-h" in cmd
        assert "db-host" in cmd
        assert "-U" in cmd
        assert "admin" in cmd
        assert "-f" in cmd
        assert "/tmp/backup.sql" in cmd
        assert "callcenter" in cmd

    def test_no_user(self) -> None:
        from src.cli.db import _build_pg_dump_cmd

        params = {
            "host": "localhost",
            "port": "5432",
            "user": "",
            "password": "",
            "dbname": "db",
        }
        cmd = _build_pg_dump_cmd(params, "/tmp/out.sql")
        assert "-U" not in cmd


class TestDbBackup:
    """Test db backup command."""

    @patch("src.cli.db.subprocess.run")
    def test_backup_success(self, mock_run: MagicMock, tmp_path: object) -> None:
        def create_dump(cmd: list[str], **kwargs: object) -> MagicMock:
            # Find the -f flag to get the output path and create the file
            f_idx = cmd.index("-f")
            output = Path(cmd[f_idx + 1])
            output.write_text("-- dump")
            return MagicMock(returncode=0)

        mock_run.side_effect = create_dump
        result = runner.invoke(app, ["db", "backup", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Backup created" in result.output
        mock_run.assert_called_once()

    @patch("src.cli.db.subprocess.run", side_effect=FileNotFoundError)
    def test_backup_pg_dump_not_found(self, mock_run: MagicMock, tmp_path: object) -> None:
        result = runner.invoke(app, ["db", "backup", "--output-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "pg_dump not found" in result.output


class TestDbRestore:
    """Test db restore command."""

    def test_restore_file_not_found(self) -> None:
        result = runner.invoke(app, ["db", "restore", "/nonexistent/file.sql"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestMigrationsStatus:
    """Test migrations status command."""

    @patch("src.cli.db.subprocess.run")
    def test_shows_current_revision(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123 (head)\n", stderr="")
        result = runner.invoke(app, ["db", "migrations-status"])
        assert result.exit_code == 0
        assert "abc123" in result.output

    @patch("src.cli.db.subprocess.run", side_effect=FileNotFoundError)
    def test_alembic_not_found(self, mock_run: MagicMock) -> None:
        result = runner.invoke(app, ["db", "migrations-status"])
        assert result.exit_code == 1
        assert "alembic not found" in result.output
