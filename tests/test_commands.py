from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import discord

from pnp_bot.bot import RegistrationBot
from pnp_bot.commands import RegistrationCommands, send_paginated_response
from pnp_bot.config import Settings
from pnp_bot.dates import schedule_for_month
from pnp_bot.views import Paginator


@pytest.mark.asyncio
async def test_expected_command_groups_can_be_registered(tmp_path: Path) -> None:
    settings = Settings(
        token="test", guild_id=1, announcement_channel_id=2, organizer_role_id=3,
        timezone=ZoneInfo("Europe/Berlin"), database_path=tmp_path / "prod.sqlite3",
        debug_database_path=tmp_path / "debug.sqlite3", debug_enabled=True, log_level="INFO",
    )
    bot = RegistrationBot(settings)
    try:
        await bot.add_cog(RegistrationCommands(bot))
        commands = {command.name: command for command in bot.tree.get_commands()}
        assert {"anmelden", "abmelden", "anmeldungen", "monatsabfrage", "debug"} <= commands.keys()
        assert {command.name for command in commands["monatsabfrage"].commands} == {
            "planungsstart", "planungsende", "status"
        }
        assert {command.name for command in commands["debug"].commands} == {
            "planungsstart", "planungsende", "status", "diagnose", "buttontest", "zuruecksetzen"
        }
    finally:
        await bot.close()


def test_persistent_signup_button_uses_requested_label(tmp_path: Path) -> None:
    settings = Settings(
        token="test", guild_id=1, announcement_channel_id=2, organizer_role_id=3,
        timezone=ZoneInfo("Europe/Berlin"), database_path=tmp_path / "prod.sqlite3",
        debug_database_path=tmp_path / "debug.sqlite3", debug_enabled=True, log_level="INFO",
    )
    bot = RegistrationBot(settings)
    labels = [item.label for item in bot.signup_views["production"].children]
    assert "Anmelden / Bearbeiten" in labels


@pytest.mark.asyncio
async def test_deleted_messages_are_not_reported_as_restored(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        token="test", guild_id=1, announcement_channel_id=2, organizer_role_id=3,
        timezone=ZoneInfo("Europe/Berlin"), database_path=tmp_path / "prod.sqlite3",
        debug_database_path=tmp_path / "debug.sqlite3", debug_enabled=True, log_level="INFO",
    )
    bot = RegistrationBot(settings)
    try:
        await bot.production_db.initialize()
        await bot.debug_db.initialize()
        survey, _ = await bot.production_db.get_or_create_survey(
            1, 2, schedule_for_month(2099, 8, settings.timezone)
        )
        await bot.production_db.set_message(survey.id, 123)

        async def message_is_missing(_survey):
            return False

        monkeypatch.setattr(bot, "_survey_message_exists", message_is_missing)
        assert await bot._restore_message_views() == (0, 1, 0)
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_single_page_response_omits_none_view() -> None:
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(response=response, user=SimpleNamespace(id=1))
    page = discord.Embed(title="Eine Seite")

    await send_paginated_response(interaction, [page])

    response.send_message.assert_awaited_once_with(embed=page, ephemeral=True)


@pytest.mark.asyncio
async def test_multiple_pages_include_paginator() -> None:
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(response=response, user=SimpleNamespace(id=1))
    pages = [discord.Embed(title="Seite 1"), discord.Embed(title="Seite 2")]

    await send_paginated_response(interaction, pages)

    kwargs = response.send_message.await_args.kwargs
    assert kwargs["embed"] is pages[0]
    assert isinstance(kwargs["view"], Paginator)
    assert kwargs["ephemeral"] is True
