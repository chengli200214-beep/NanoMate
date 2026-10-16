"""Scheduling calculations shared by the API and the durable worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nanomuse.scheduling import civil_occurrence


def next_check(now: float, tracking: dict[str, Any]) -> float:
    name = tracking["timezone"]
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ValueError("Unknown timezone")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("Unknown timezone") from exc
    if not tracking["daily_time"]:
        return now + tracking["interval_minutes"] * 60
    try:
        hour, minute = map(int, tracking["daily_time"].split(":"))
        local = datetime.fromtimestamp(now, UTC).astimezone(zone)
        for offset in range(3):
            due = civil_occurrence(local.date() + timedelta(days=offset), hour, minute, zone)
            if due is not None and due.timestamp() > now:
                return due.timestamp()
    except (ValueError, TypeError) as exc:
        raise ValueError("Daily check time must use HH:mm") from exc
    raise ValueError("No valid daily check time in the next three dates")
