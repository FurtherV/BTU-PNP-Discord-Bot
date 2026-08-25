from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
import discord

from pnp_bot.bot import RegistrationBot
from pnp_bot.commands import RegistrationCommands, send_paginated_response
from pnp_bot.config import Settings
from pnp_bot.dates import schedule_for_month
from pnp_bot.views import Paginator


def command_cog() -> RegistrationCommands:
    bot = SimpleNamespace(
        settings=SimpleNamespace(guild_id=1, organizer_role_id=3, debug_enabled=False)
    )
    return RegistrationCommands(bot)


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
        assert {"anmelden", "abmelden", "anmeldungen", "monatsabfrage", "debug", "kanal"} <= commands.keys()
        assert {command.name for command in commands["monatsabfrage"].commands} == {
            "planungsstart", "planungsende", "status"
        }
        assert {command.name for command in commands["debug"].commands} == {
            "planungsstart", "planungsende", "status", "diagnose", "buttontest", "zuruecksetzen"
        }
        assert {command.name for command in commands["kanal"].commands} == {
            "sperren", "entsperren"
        }
        assert commands["kanal"].guild_only is True
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


@pytest.mark.asyncio
async def test_admin_check_rejects_another_guild() -> None:
    interaction = SimpleNamespace(
        guild_id=99,
        user=SimpleNamespace(),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert not await command_cog()._admin(interaction)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_check_accepts_configured_organizer_role() -> None:
    member = Mock(spec=discord.Member)
    member.guild_permissions = SimpleNamespace(administrator=False)
    member.roles = [SimpleNamespace(id=3)]
    interaction = SimpleNamespace(
        guild_id=1,
        user=member,
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert await command_cog()._admin(interaction)
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_lock_edits_only_everyone_overwrite() -> None:
    everyone = object()
    member = object()
    channel = Mock(spec=discord.TextChannel)
    channel.mention = "#gruppe"
    channel.overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel.permissions_for.return_value = SimpleNamespace(manage_roles=True)
    channel.edit = AsyncMock()
    guild = SimpleNamespace(me=object(), default_role=everyone)
    interaction = SimpleNamespace(
        channel=channel,
        guild=guild,
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    cog = command_cog()
    cog._admin = AsyncMock(return_value=True)

    await cog._set_channel_lock(interaction, locked=True)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    channel.edit.assert_awaited_once()
    updated = channel.edit.await_args.kwargs["overwrites"]
    assert updated[everyone].view_channel is False
    assert updated[everyone].send_messages is False
    assert updated[everyone].send_messages_in_threads is False
    assert updated[member].view_channel is True
    assert updated[member].send_messages is True


@pytest.mark.asyncio
async def test_channel_lock_requires_manage_roles() -> None:
    channel = Mock(spec=discord.TextChannel)
    channel.permissions_for.return_value = SimpleNamespace(manage_roles=False)
    guild = SimpleNamespace(me=object(), default_role=object())
    interaction = SimpleNamespace(
        channel=channel,
        guild=guild,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    cog = command_cog()
    cog._admin = AsyncMock(return_value=True)

    await cog._set_channel_lock(interaction, locked=True)

    interaction.response.send_message.assert_awaited_once()
    channel.edit.assert_not_called()
