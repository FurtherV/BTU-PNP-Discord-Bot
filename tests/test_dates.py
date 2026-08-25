from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pnp_bot.dates import german_month, parse_month, schedule_for_month


TZ = ZoneInfo("Europe/Berlin")


def test_last_full_week_when_month_ends_on_monday() -> None:
    schedule = schedule_for_month(2026, 8, TZ)
    assert schedule.week_start.isoformat() == "2026-08-24"
    assert schedule.week_end.isoformat() == "2026-08-30"
    assert schedule.deadline.isoformat() == "2026-08-17T23:59:00+02:00"
    assert schedule.announcement_at.isoformat() == "2026-08-01T09:00:00+02:00"


def test_last_full_week_in_leap_february() -> None:
    schedule = schedule_for_month(2024, 2, TZ)
    assert schedule.week_start.isoformat() == "2024-02-19"
    assert schedule.week_end.isoformat() == "2024-02-25"
    assert schedule.deadline.isoformat() == "2024-02-12T23:59:00+01:00"


def test_month_ending_on_sunday_uses_final_seven_days() -> None:
    schedule = schedule_for_month(2026, 5, TZ)
    assert schedule.week_start.isoformat() == "2026-05-25"
    assert schedule.week_end.isoformat() == "2026-05-31"


def test_parse_month() -> None:
    now = datetime(2026, 8, 17, tzinfo=TZ)
    assert parse_month(None, now) == (2026, 8)
    assert parse_month("2027-01", now) == (2027, 1)
    with pytest.raises(ValueError):
        parse_month("01/2027", now)


def test_german_month() -> None:
    assert german_month(8) == "August"
    with pytest.raises(ValueError):
        german_month(13)
