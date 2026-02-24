"""Unit tests for task schedule utilities."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from src.tasks.schedule_utils import (
    KYIV_TZ,
    REDIS_KEY,
    TASK_DEFAULTS,
    load_schedules,
    save_schedules,
    should_run_now,
)


class TestShouldRunNow:
    """Tests for should_run_now()."""

    def test_daily_correct_hour(self) -> None:
        """Daily task should run when current hour matches."""
        schedule = {"enabled": True, "frequency": "daily", "hour": 8, "day_of_week": 0}
        now = datetime(2026, 2, 24, 8, 15, tzinfo=KYIV_TZ)  # Tuesday 08:15
        assert should_run_now(schedule, now=now) is True

    def test_daily_wrong_hour(self) -> None:
        """Daily task should not run when hour doesn't match."""
        schedule = {"enabled": True, "frequency": "daily", "hour": 8, "day_of_week": 0}
        now = datetime(2026, 2, 24, 10, 0, tzinfo=KYIV_TZ)  # Tuesday 10:00
        assert should_run_now(schedule, now=now) is False

    def test_weekly_correct_day_and_hour(self) -> None:
        """Weekly task should run when both day and hour match."""
        schedule = {"enabled": True, "frequency": "weekly", "hour": 10, "day_of_week": 6}
        now = datetime(2026, 3, 1, 10, 0, tzinfo=KYIV_TZ)  # Sunday (weekday=6) 10:00
        assert should_run_now(schedule, now=now) is True

    def test_weekly_wrong_day(self) -> None:
        """Weekly task should not run on wrong day even if hour matches."""
        schedule = {"enabled": True, "frequency": "weekly", "hour": 10, "day_of_week": 6}
        now = datetime(2026, 2, 24, 10, 0, tzinfo=KYIV_TZ)  # Tuesday (weekday=1) 10:00
        assert should_run_now(schedule, now=now) is False

    def test_weekly_wrong_hour(self) -> None:
        """Weekly task should not run at wrong hour even on correct day."""
        schedule = {"enabled": True, "frequency": "weekly", "hour": 10, "day_of_week": 6}
        now = datetime(2026, 3, 1, 14, 0, tzinfo=KYIV_TZ)  # Sunday 14:00
        assert should_run_now(schedule, now=now) is False

    def test_disabled_schedule(self) -> None:
        """Disabled schedule should always return False."""
        schedule = {"enabled": False, "frequency": "daily", "hour": 8, "day_of_week": 0}
        now = datetime(2026, 2, 24, 8, 0, tzinfo=KYIV_TZ)
        assert should_run_now(schedule, now=now) is False

    def test_defaults_to_enabled(self) -> None:
        """Schedule without enabled key should default to enabled."""
        schedule = {"frequency": "daily", "hour": 8}
        now = datetime(2026, 2, 24, 8, 0, tzinfo=KYIV_TZ)
        assert should_run_now(schedule, now=now) is True

    def test_naive_datetime_treated_as_kyiv(self) -> None:
        """Naive datetime should be treated as Kyiv timezone."""
        schedule = {"enabled": True, "frequency": "daily", "hour": 8}
        now = datetime(2026, 2, 24, 8, 30)  # naive
        assert should_run_now(schedule, now=now) is True

    def test_uses_current_time_when_none(self) -> None:
        """When now is None, should use current Kyiv time (no error)."""
        schedule = {"enabled": True, "frequency": "daily", "hour": 99}
        # hour=99 is impossible, so this always returns False
        assert should_run_now(schedule) is False


class TestLoadSchedules:
    """Tests for load_schedules()."""

    @pytest.mark.asyncio
    async def test_returns_defaults_when_redis_empty(self) -> None:
        """Should return TASK_DEFAULTS when Redis has no data."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)

        result = await load_schedules(redis)

        assert "catalog-full-sync" in result
        assert "refresh-stt-hints" in result
        assert result["catalog-full-sync"]["hour"] == 8
        assert result["refresh-stt-hints"]["frequency"] == "weekly"

    @pytest.mark.asyncio
    async def test_merges_redis_overrides(self) -> None:
        """Should merge Redis overrides with defaults."""
        overrides = {"catalog-full-sync": {"hour": 10, "enabled": False}}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(overrides))

        result = await load_schedules(redis)

        # Overridden fields
        assert result["catalog-full-sync"]["hour"] == 10
        assert result["catalog-full-sync"]["enabled"] is False
        # Default fields preserved
        assert result["catalog-full-sync"]["frequency"] == "daily"
        assert result["catalog-full-sync"]["label"] == "Полная синхронизация каталога 1С"
        # Other task untouched
        assert result["refresh-stt-hints"]["hour"] == 10

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(self) -> None:
        """Should return defaults if Redis read fails."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=Exception("connection refused"))

        result = await load_schedules(redis)

        assert len(result) == len(TASK_DEFAULTS)
        assert result["catalog-full-sync"]["hour"] == 8

    @pytest.mark.asyncio
    async def test_handles_bytes_from_redis(self) -> None:
        """Should handle bytes response from Redis (decode_responses=False)."""
        overrides = {"refresh-stt-hints": {"day_of_week": 0}}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(overrides).encode())

        result = await load_schedules(redis)
        assert result["refresh-stt-hints"]["day_of_week"] == 0


class TestSaveSchedules:
    """Tests for save_schedules()."""

    @pytest.mark.asyncio
    async def test_saves_to_redis(self) -> None:
        """Should save schedules as JSON to Redis."""
        redis = AsyncMock()
        redis.set = AsyncMock()

        schedules = {"catalog-full-sync": {"hour": 12}}
        await save_schedules(redis, schedules)

        redis.set.assert_called_once()
        call_args = redis.set.call_args
        assert call_args[0][0] == REDIS_KEY
        saved = json.loads(call_args[0][1])
        assert saved["catalog-full-sync"]["hour"] == 12
