"""Scheduling calculations shared by the API and the durable worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, TypeError) as exc:
        raise ValueError("Daily check time must use HH:mm") from exc
    if due.timestamp() <= now:
        due += timedelta(days=1)
    return due.timestamp()
