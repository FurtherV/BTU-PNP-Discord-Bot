from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Survey:
    id: int
    guild_id: int
    year: int
    month: int
    channel_id: int
    message_id: int | None
    week_start: date
    week_end: date
    deadline: datetime
    state: str
    created_at: datetime
    closed_at: datetime | None
    reminder_last_run_at: datetime | None
    reminder_completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class Registration:
    id: int
    survey_id: int
    user_id: int
    display_name: str
    is_player: bool
    is_dm: bool
    notes: str
    available_dates: tuple[date, ...]
    created_at: datetime
    updated_at: datetime
