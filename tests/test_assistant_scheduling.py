"""Civil-time transition tests use explicit zones without changing the host clock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nanomuse.goals.store import next_check_in
from nanomuse.research.schedule import next_check
from nanomuse.scheduling import civil_occurrence


@pytest.mark.parametrize(
    ("zone_name", "day", "hour", "minute"),
    [
        ("America/New_York", date(2026, 3, 8), 2, 30),
        ("Europe/Berlin", date(2026, 3, 29), 2, 30),
        ("Australia/Lord_Howe", date(2026, 10, 4), 2, 15),
    ],
)
def test_f18_nonexistent_civil_time_is_skipped(
    zone_name: str, day: date, hour: int, minute: int
) -> None:
    zone = ZoneInfo(zone_name)
    assert civil_occurrence(day, hour, minute, zone) is None
    now = datetime(day.year, day.month, day.day, 0, tzinfo=zone)
    actual = next_check_in(f"daily {hour:02}:{minute:02}", now)
    next_day = day + timedelta(days=1)
    expected = datetime(next_day.year, next_day.month, next_day.day, hour, minute, tzinfo=zone)
    assert actual == expected and actual.tzinfo is zone
    tracking = {"timezone": zone_name, "daily_time": f"{hour:02}:{minute:02}"}
    assert next_check(now.timestamp(), tracking) == expected.timestamp()


@pytest.mark.parametrize(
    ("zone_name", "day", "hour", "minute"),
    [
        ("America/New_York", date(2026, 11, 1), 1, 30),
        ("Europe/Berlin", date(2026, 10, 25), 2, 30),
        ("Australia/Lord_Howe", date(2026, 4, 5), 1, 45),
    ],
)
def test_f18_ambiguous_time_uses_first_occurrence_and_does_not_repeat(
    zone_name: str, day: date, hour: int, minute: int
) -> None:
    zone = ZoneInfo(zone_name)
    start = datetime(day.year, day.month, day.day, 0, tzinfo=zone)
    first = civil_occurrence(day, hour, minute, zone)
    assert first is not None and first.fold == 0
    second = first.replace(fold=1)
    assert second.timestamp() > first.timestamp()
    tracking = {"timezone": zone_name, "daily_time": f"{hour:02}:{minute:02}"}
    assert next_check(start.timestamp(), tracking) == first.timestamp()
    # At both instants, the next event must be tomorrow, never the second fold.
    expected_day = day + timedelta(days=1)
    expected = datetime(
        expected_day.year, expected_day.month, expected_day.day, hour, minute, tzinfo=zone
    )
    for dispatched in (first, second):
        actual = next_check_in(f"daily {hour:02}:{minute:02}", dispatched)
        assert actual is not None and actual.timestamp() == expected.timestamp()
        assert next_check(dispatched.timestamp(), tracking) == expected.timestamp()


def test_f18_daily_time_preserves_zone_rules_instead_of_current_fixed_offset() -> None:
    zone = ZoneInfo("America/New_York")
    before_spring = datetime(2026, 3, 7, 9, tzinfo=zone)
    spring = next_check_in("daily 09:00", before_spring)
    assert spring is not None and spring.tzinfo is zone
    assert spring.astimezone(UTC) == datetime(2026, 3, 8, 13, tzinfo=UTC)
    assert spring.timestamp() - before_spring.timestamp() == 23 * 3600

    before_autumn = datetime(2026, 10, 31, 9, tzinfo=zone)
    autumn = next_check_in("daily 09:00", before_autumn)
    assert autumn is not None and autumn.tzinfo is zone
    assert autumn.astimezone(UTC) == datetime(2026, 11, 1, 14, tzinfo=UTC)
    assert autumn.timestamp() - before_autumn.timestamp() == 25 * 3600


def test_f18_weekly_gap_skips_to_next_matching_weekday() -> None:
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 3, 7, 9, tzinfo=zone)
    actual = next_check_in("weekly sun 02:30", now)
    assert actual == datetime(2026, 3, 15, 2, 30, tzinfo=zone)


def test_f18_local_resolution_uses_os_rules_for_each_date() -> None:
    # The current machine need not observe DST. This checks the local resolution
    # contract without assigning TZ or changing any system setting.
    day = date(2026, 10, 18)
    expected = datetime(day.year, day.month, day.day, 9, 0).astimezone()
    actual = civil_occurrence(day, 9, 0)
    assert actual is not None and actual.timestamp() == expected.timestamp()


@pytest.mark.parametrize("value", ["25:00", "09:90", "bad"])
def test_f18_research_daily_time_validation_remains_bounded(value: str) -> None:
    with pytest.raises(ValueError, match="HH:mm"):
        next_check(0, {"timezone": "America/New_York", "daily_time": value})
