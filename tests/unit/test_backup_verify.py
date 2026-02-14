"""Unit tests for backup verification."""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path

from src.tasks.backup import verify_backup


class TestVerifyBackup:
    """Test verify_backup function."""

    def test_verify_valid_sql(self, tmp_path: Path) -> None:
        sql_file = tmp_path / "test.sql"
        sql_file.write_text("-- PostgreSQL database dump\nCREATE TABLE foo (id int);")
        result = verify_backup(sql_file)
        assert result["status"] == "ok"
        assert result["compressed"] is False

    def test_verify_valid_gzipped(self, tmp_path: Path) -> None:
        gz_file = tmp_path / "test.sql.gz"
        content = b"-- PostgreSQL database dump\nCREATE TABLE foo (id int);\n" * 100
        with gzip.open(gz_file, "wb") as f:
            f.write(content)
        result = verify_backup(gz_file)
        assert result["status"] == "ok"
        assert result["compressed"] is True
        assert result["uncompressed_bytes"] > 0

    def test_verify_missing_file(self) -> None:
        result = verify_backup("/nonexistent/file.sql")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_verify_invalid_content(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.sql"
        bad_file.write_text("This is not a valid SQL dump at all, just random text.")
        result = verify_backup(bad_file)
        assert result["status"] == "error"
        assert "valid PostgreSQL dump" in result["error"]

    def test_verify_corrupt_gzip(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.sql.gz"
        corrupt.write_bytes(b"\x1f\x8b\x08\x00corrupt data")
        result = verify_backup(corrupt)
        assert result["status"] == "error"
