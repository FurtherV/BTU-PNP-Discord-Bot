from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class MonthSchedule:
    year: int
    month: int
    week_start: date
    week_end: date
    announcement_at: datetime
    deadline: datetime

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(self.week_start + timedelta(days=offset) for offset in range(7))


def schedule_for_month(year: int, month: int, timezone: ZoneInfo) -> MonthSchedule:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    last_sunday = last_day - timedelta(days=(last_day.weekday() - 6) % 7)
    week_start = last_sunday - timedelta(days=6)
    announcement_at = datetime.combine(date(year, month, 1), time(9, 0), timezone)
    deadline_day = week_start - timedelta(days=7)
    deadline = datetime.combine(deadline_day, time(23, 59), timezone)
    return MonthSchedule(year, month, week_start, last_sunday, announcement_at, deadline)


def parse_month(value: str | None, now: datetime) -> tuple[int, int]:
    if value is None or not value.strip():
        return now.year, now.month
    try:
        parsed = datetime.strptime(value.strip(), "%Y-%m")
    except ValueError as exc:
        raise ValueError("Der Monat muss das Format YYYY-MM haben, z. B. 2026-08.") from exc
    return parsed.year, parsed.month


def german_date(value: date) -> str:
    weekdays = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    return f"{weekdays[value.weekday()]}, {value:%d.%m.%Y}"


def german_month(month: int) -> str:
    months = (
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    )
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return months[month - 1]
