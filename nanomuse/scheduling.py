"""Resolve civil-time occurrences without freezing a timezone's current offset."""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo


def civil_occurrence(
    day: date, hour: int, minute: int, zone: tzinfo | None = None
) -> datetime | None:
    """Use the first occurrence of an ambiguous time and skip nonexistent times.

    With no explicit zone, the OS resolves each date in its local timezone. A
    UTC round trip detects DST gaps, including transitions other than one hour.
    The function never changes the process timezone or the machine clock.
    """
    wall = datetime(day.year, day.month, day.day, hour, minute)
    first = wall.replace(tzinfo=zone, fold=0) if zone is not None else wall.astimezone()
    returned = first.astimezone(UTC).astimezone(zone)
    if returned.replace(tzinfo=None) != wall:
        return None
    return first
