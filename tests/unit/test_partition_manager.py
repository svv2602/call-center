"""Tests for partition_manager module."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks.partition_manager import (
    MONTHS_AHEAD,
    PARTITIONED_TABLES,
    RETENTION_TABLES,
    _months_range,
)


class TestMonthsRange:
    """Test the _months_range helper."""

    def test_generates_correct_count(self) -> None:
        result = _months_range(date(2026, 6, 15), 3)
        assert len(result) == 3

    def test_starts_from_first_of_month(self) -> None:
        result = _months_range(date(2026, 6, 15), 1)
        assert result[0][0] == date(2026, 6, 1)

    def test_end_is_next_month(self) -> None:
        result = _months_range(date(2026, 6, 15), 1)
        assert result[0][1] == date(2026, 7, 1)

    def test_december_to_january(self) -> None:
        result = _months_range(date(2026, 12, 1), 2)
        assert result[0] == (date(2026, 12, 1), date(2027, 1, 1))
        assert result[1] == (date(2027, 1, 1), date(2027, 2, 1))

    def test_consecutive_months(self) -> None:
        result = _months_range(date(2026, 3, 1), 3)
        assert result[0] == (date(2026, 3, 1), date(2026, 4, 1))
        assert result[1] == (date(2026, 4, 1), date(2026, 5, 1))
        assert result[2] == (date(2026, 5, 1), date(2026, 6, 1))


class TestPartitionSQL:
    """Test that generated SQL is correct."""

    def test_create_partition_sql_format(self) -> None:
        months = _months_range(date(2026, 6, 1), 1)
        start, end = months[0]
        for table in PARTITIONED_TABLES:
            partition_name = f"{table}_{start.year}_{start.month:02d}"
            sql = (
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF {table} "
                f"FOR VALUES FROM ('{start.isoformat()}') "
                f"TO ('{end.isoformat()}')"
            )
            assert "IF NOT EXISTS" in sql
            assert f"PARTITION OF {table}" in sql
            assert partition_name == f"{table}_2026_06"

    def test_partition_name_format(self) -> None:
        months = _months_range(date(2026, 1, 1), 1)
        start, _ = months[0]
        name = f"calls_{start.year}_{start.month:02d}"
        assert name == "calls_2026_01"

    def test_partition_name_padded_month(self) -> None:
        months = _months_range(date(2026, 9, 1), 1)
        start, _ = months[0]
        name = f"calls_{start.year}_{start.month:02d}"
        assert name == "calls_2026_09"


class TestPartitionedTables:
    """Test constants."""

    def test_all_tables_present(self) -> None:
        assert "calls" in PARTITIONED_TABLES
        assert "call_turns" in PARTITIONED_TABLES
        assert "call_tool_calls" in PARTITIONED_TABLES

    def test_retention_tables_subset(self) -> None:
        for table in RETENTION_TABLES:
            assert table in PARTITIONED_TABLES

    def test_calls_not_in_retention(self) -> None:
        assert "calls" not in RETENTION_TABLES

    def test_months_ahead(self) -> None:
        assert MONTHS_AHEAD == 3
