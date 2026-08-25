from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pnp_bot.config import ConfigurationError, Settings, _optional_month


def _settings(start_month: tuple[int, int] | None = None) -> Settings:
    return Settings(
        token="test",
        guild_id=1,
        announcement_channel_id=2,
        organizer_role_id=3,
        timezone=ZoneInfo("Europe/Berlin"),
        database_path=Path("production.sqlite3"),
        debug_database_path=Path("debug.sqlite3"),
        debug_enabled=False,
        log_level="INFO",
        automation_start_month=start_month,
    )


def test_missing_automation_start_month_keeps_previous_behavior(monkeypatch) -> None:
    monkeypatch.delenv("AUTOMATION_START_MONTH", raising=False)

    assert _optional_month("AUTOMATION_START_MONTH") is None
    assert _settings().allows_automatic_survey(2026, 8)


def test_automatic_surveys_start_with_configured_month() -> None:
    settings = _settings((2026, 9))

    assert not settings.allows_automatic_survey(2026, 8)
    assert settings.allows_automatic_survey(2026, 9)
    assert settings.allows_automatic_survey(2027, 1)


@pytest.mark.parametrize("value", ["2026-9", "09/2026", "2026-13", "text"])
def test_automation_start_month_requires_year_month_format(monkeypatch, value: str) -> None:
    monkeypatch.setenv("AUTOMATION_START_MONTH", value)

    with pytest.raises(ConfigurationError, match="YYYY-MM"):
        _optional_month("AUTOMATION_START_MONTH")
