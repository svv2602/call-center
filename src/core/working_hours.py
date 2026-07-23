"""Tenant working-hours check for after-hours call routing.

Structure of `working_hours` (as stored in `tenants.working_hours` JSONB):
    {
      "timezone": "Europe/Kyiv",
      "mon": {"start": "09:00", "end": "18:00"},
      ...
      "sat": {"start": "10:00", "end": "16:00"},
      "sun": null
    }

`null` for a day = closed. Missing day keys are treated the same as `null`.
If `working_hours` itself is None → treat as 24/7 (skip the check).
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DEFAULT_TZ = "Europe/Kyiv"


def _get_tz(working_hours: dict[str, Any]) -> ZoneInfo:
    name = working_hours.get("timezone") or _DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r, falling back to %s", name, _DEFAULT_TZ)
        return ZoneInfo(_DEFAULT_TZ)


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":", 1)
    return time(int(hours), int(minutes))


def _day_schedule(working_hours: dict[str, Any], weekday: int) -> dict[str, str] | None:
    """Return {"start": "HH:MM", "end": "HH:MM"} or None (closed)."""
    key = _DAY_KEYS[weekday]
    raw = working_hours.get(key)
    if raw is None:
        return None
    if not isinstance(raw, dict) or "start" not in raw or "end" not in raw:
        return None
    return raw


def is_open(working_hours: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Return True if the tenant accepts calls at the given moment.

    `working_hours=None` → 24/7 (backward compat for tenants without a schedule).
    """
    if not working_hours:
        return True
    tz = _get_tz(working_hours)
    now = datetime.now(tz) if now is None else now.astimezone(tz)
    schedule = _day_schedule(working_hours, now.weekday())
    if schedule is None:
        return False
    try:
        start = _parse_hhmm(schedule["start"])
        end = _parse_hhmm(schedule["end"])
    except (ValueError, KeyError):
        logger.warning("Malformed schedule %r for %s", schedule, _DAY_KEYS[now.weekday()])
        return False
    current = now.time().replace(second=0, microsecond=0)
    return start <= current < end


def next_open_time(
    working_hours: dict[str, Any] | None, now: datetime | None = None
) -> datetime | None:
    """Return the next moment the tenant opens, or None if 24/7 / no schedule."""
    if not working_hours:
        return None
    tz = _get_tz(working_hours)
    now = datetime.now(tz) if now is None else now.astimezone(tz)

    # Look up to 8 days ahead (today + 7) to cover a full week including today
    for offset in range(8):
        candidate_date: date_type = (now + timedelta(days=offset)).date()
        schedule = _day_schedule(working_hours, candidate_date.weekday())
        if schedule is None:
            continue
        try:
            start = _parse_hhmm(schedule["start"])
        except ValueError:
            continue
        candidate = datetime.combine(candidate_date, start, tzinfo=tz)
        if candidate > now:
            return candidate
    return None


def format_hours_for_speech(working_hours: dict[str, Any] | None) -> str:
    """Compact Ukrainian summary suitable for TTS.

    Examples:
        "Пн–Пт 9:00–18:00, Сб 10:00–16:00, Нд вихідний"
        "Пн–Пт 9:00–18:00, Сб–Нд вихідний"
        "Щодня 9:00–18:00"
    """
    if not working_hours:
        return "цілодобово"

    day_labels_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

    schedules: list[str | None] = []
    for i, key in enumerate(_DAY_KEYS):
        raw = working_hours.get(key)
        if raw is None or not isinstance(raw, dict):
            schedules.append(None)
        else:
            start = _strip_leading_zero(raw.get("start", ""))
            end = _strip_leading_zero(raw.get("end", ""))
            if start and end:
                schedules.append(f"{start}–{end}")
            else:
                schedules.append(None)
        del i, key

    # Group consecutive identical days
    groups: list[tuple[int, int, str | None]] = []
    i = 0
    while i < len(schedules):
        j = i
        while j + 1 < len(schedules) and schedules[j + 1] == schedules[i]:
            j += 1
        groups.append((i, j, schedules[i]))
        i = j + 1

    parts: list[str] = []
    for start_i, end_i, sched in groups:
        if start_i == end_i:
            day_label = day_labels_short[start_i]
        else:
            day_label = f"{day_labels_short[start_i]}–{day_labels_short[end_i]}"
        if sched is None:
            parts.append(f"{day_label} вихідний")
        else:
            parts.append(f"{day_label} {sched}")

    return ", ".join(parts)


def format_next_open_for_speech(when: datetime | None) -> str:
    """Ukrainian phrase for the reopening time, e.g. 'завтра з 9:00' or 'в понеділок з 9:00'."""
    if when is None:
        return ""
    tz = when.tzinfo or ZoneInfo(_DEFAULT_TZ)
    now = datetime.now(tz)
    today = now.date()
    target_date = when.date()
    delta_days = (target_date - today).days
    hhmm = _strip_leading_zero(when.strftime("%H:%M"))

    if delta_days == 0:
        return f"сьогодні з {hhmm}"
    if delta_days == 1:
        return f"завтра з {hhmm}"
    weekday_names = [
        "в понеділок",
        "у вівторок",
        "в середу",
        "в четвер",
        "в п'ятницю",
        "в суботу",
        "в неділю",
    ]
    return f"{weekday_names[target_date.weekday()]} з {hhmm}"


def _strip_leading_zero(hhmm: str) -> str:
    """9:00 instead of 09:00 — reads more naturally aloud."""
    if hhmm.startswith("0"):
        return hhmm[1:]
    return hhmm


def validate_schema(working_hours: Any) -> None:
    """Raise ValueError if the JSON doesn't match the expected shape.

    Called from the tenants PATCH API before saving. `None` is valid
    (means 24/7). Missing day keys are allowed and treated as closed.
    """
    if working_hours is None:
        return
    if not isinstance(working_hours, dict):
        msg = "working_hours must be an object or null"
        raise ValueError(msg)

    tz_name = working_hours.get("timezone")
    if tz_name is not None:
        if not isinstance(tz_name, str):
            msg = "timezone must be a string"
            raise ValueError(msg)
        try:
            ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            msg = f"unknown timezone: {tz_name!r}"
            raise ValueError(msg) from exc

    for key in _DAY_KEYS:
        if key not in working_hours:
            continue
        raw = working_hours[key]
        if raw is None:
            continue
        if not isinstance(raw, dict):
            msg = f"{key} must be an object with start/end or null"
            raise ValueError(msg)
        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            msg = f"{key}.start and {key}.end must be strings"
            raise ValueError(msg)
        try:
            start_t = _parse_hhmm(start)
            end_t = _parse_hhmm(end)
        except (ValueError, IndexError) as exc:
            msg = f"{key}: times must be in HH:MM format"
            raise ValueError(msg) from exc
        if end_t <= start_t:
            msg = f"{key}: end ({end}) must be after start ({start})"
            raise ValueError(msg)
