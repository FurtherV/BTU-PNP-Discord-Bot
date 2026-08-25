from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pnp_bot.database import Database
from pnp_bot.dates import schedule_for_month


TZ = ZoneInfo("Europe/Berlin")


@pytest.mark.asyncio
async def test_registration_lifecycle(tmp_path) -> None:
    database = Database(tmp_path / "registrations.sqlite3")
    await database.initialize()
    schedule = schedule_for_month(2099, 8, TZ)
    survey, created = await database.get_or_create_survey(1, 2, schedule)
    assert created

    now = datetime(2099, 8, 2, 12, tzinfo=TZ)
    first = await database.save_registration(
        survey.id, 42, "Alice", True, False, "Notiz",
        (schedule.week_start, schedule.week_end), now,
    )
    assert first.is_player and not first.is_dm
    assert len(first.available_dates) == 2

    updated = await database.save_registration(
        survey.id, 42, "Alice Neu", True, True, "Geändert",
        (schedule.week_start,), now,
    )
    assert updated.id == first.id
    assert updated.display_name == "Alice Neu"
    assert updated.is_player and updated.is_dm
    assert len(await database.list_registrations(survey.id)) == 1

    assert await database.delete_registration(survey.id, 42, now)
    assert await database.get_registration(survey.id, 42) is None


@pytest.mark.asyncio
async def test_closed_survey_rejects_changes(tmp_path) -> None:
    database = Database(tmp_path / "closed.sqlite3")
    await database.initialize()
    schedule = schedule_for_month(2099, 8, TZ)
    survey, _ = await database.get_or_create_survey(1, 2, schedule)
    now = datetime(2099, 8, 2, 12, tzinfo=TZ)
    await database.close_survey(survey.id, now)
    with pytest.raises(PermissionError):
        await database.save_registration(
            survey.id, 42, "Alice", True, False, "", (schedule.week_start,), now
        )


@pytest.mark.asyncio
async def test_invalid_availability_is_rejected(tmp_path) -> None:
    database = Database(tmp_path / "invalid.sqlite3")
    await database.initialize()
    schedule = schedule_for_month(2099, 8, TZ)
    survey, _ = await database.get_or_create_survey(1, 2, schedule)
    with pytest.raises(ValueError):
        await database.save_registration(
            survey.id, 42, "Alice", True, False, "",
            (date(2099, 8, 1),), datetime(2099, 8, 2, 12, tzinfo=TZ),
        )


@pytest.mark.asyncio
async def test_databases_are_isolated(tmp_path) -> None:
    production = Database(tmp_path / "production.sqlite3")
    debug = Database(tmp_path / "debug.sqlite3")
    await production.initialize()
    await debug.initialize()
    schedule = schedule_for_month(2099, 8, TZ)
    await production.get_or_create_survey(1, 2, schedule)
    assert await debug.get_survey(1, 2099, 8) is None


@pytest.mark.asyncio
async def test_only_open_surveys_with_messages_are_restored(tmp_path) -> None:
    database = Database(tmp_path / "views.sqlite3")
    await database.initialize()
    august, _ = await database.get_or_create_survey(1, 2, schedule_for_month(2099, 8, TZ))
    september, _ = await database.get_or_create_survey(1, 2, schedule_for_month(2099, 9, TZ))
    october, _ = await database.get_or_create_survey(1, 2, schedule_for_month(2099, 10, TZ))
    await database.get_or_create_survey(1, 2, schedule_for_month(2099, 11, TZ))
    await database.set_message(august.id, 100)
    await database.set_message(september.id, 200)
    await database.set_message(october.id, 300)
    await database.close_survey(september.id, datetime(2099, 9, 1, tzinfo=TZ))

    surveys = await database.list_open_surveys(1)

    assert [(survey.month, survey.message_id) for survey in surveys] == [(8, 100), (10, 300)]


@pytest.mark.asyncio
async def test_reminders_are_idempotent_per_recipient(tmp_path) -> None:
    database = Database(tmp_path / "reminders.sqlite3")
    await database.initialize()
    schedule = schedule_for_month(2099, 8, TZ)
    survey, _ = await database.get_or_create_survey(1, 2, schedule)
    now = datetime(2099, 8, 18, tzinfo=TZ)
    await database.prepare_reminders(survey.id, [10, 11])
    await database.record_reminder(survey.id, 10, now, None)
    await database.record_reminder(survey.id, 11, now, "DM gesperrt")
    assert await database.pending_reminder_ids(survey.id) == [11]
    counts = await database.reminder_counts(survey.id)
    assert counts == {"pending": 0, "delivered": 1, "failed": 1}


@pytest.mark.asyncio
async def test_metadata_can_be_updated(tmp_path) -> None:
    database = Database(tmp_path / "metadata.sqlite3")
    await database.initialize()
    assert await database.get_metadata("command_hash") is None
    await database.set_metadata("command_hash", "first")
    assert await database.get_metadata("command_hash") == "first"
    await database.set_metadata("command_hash", "second")
    assert await database.get_metadata("command_hash") == "second"
