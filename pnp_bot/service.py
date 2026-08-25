from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import discord

from .database import Database
from .dates import german_date, german_month, schedule_for_month
from .models import Survey

if TYPE_CHECKING:
    from .bot import RegistrationBot

Scope = Literal["production", "debug"]
log = logging.getLogger(__name__)


class SurveyService:
    def __init__(self, bot: "RegistrationBot"):
        self.bot = bot

    def database(self, scope: Scope) -> Database:
        return self.bot.production_db if scope == "production" else self.bot.debug_db

    async def start(
        self, year: int, month: int, scope: Scope, *, refresh_existing: bool = False
    ) -> tuple[Survey, str]:
        settings = self.bot.settings
        schedule = schedule_for_month(year, month, settings.timezone)
        database = self.database(scope)
        survey, created = await database.get_or_create_survey(
            settings.guild_id, settings.announcement_channel_id, schedule
        )
        if survey.state == "closed":
            return survey, "closed"

        if survey.message_id is not None and await self._message_exists(survey):
            if refresh_existing:
                await self.refresh_buttons(survey, scope)
            return survey, "existing"

        channel = await self._announcement_channel()
        message = await channel.send(
            embed=self.announcement_embed(survey, scope),
            view=self.bot.create_signup_view(scope),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await database.set_message(survey.id, message.id)
        refreshed = await database.get_survey(settings.guild_id, year, month)
        assert refreshed is not None
        return refreshed, "created" if created else "replaced"

    async def refresh_buttons(self, survey: Survey, scope: Scope) -> None:
        if survey.message_id is None:
            raise RuntimeError("Die Monatsabfrage besitzt keinen Discord-Post.")
        channel = await self._announcement_channel()
        message = await channel.fetch_message(survey.message_id)
        await message.edit(
            embed=self.announcement_embed(survey, scope),
            view=self.bot.create_signup_view(scope),
        )

    async def close(self, survey: Survey, scope: Scope) -> dict[str, int]:
        now = datetime.now(self.bot.settings.timezone)
        database = self.database(scope)
        survey = await database.close_survey(survey.id, now)
        await database.begin_reminder_run(survey.id, now)
        await self._disable_message(survey, scope)

        guild = self.bot.get_guild(self.bot.settings.guild_id)
        if guild is None:
            raise RuntimeError("Der konfigurierte Discord-Server ist nicht verfügbar.")
        role = guild.get_role(self.bot.settings.organizer_role_id)
        if role is None:
            raise RuntimeError("Die konfigurierte Orga-Rolle wurde nicht gefunden.")

        members: dict[int, discord.Member] = {member.id: member for member in role.members if not member.bot}
        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except discord.HTTPException:
                log.exception("Mitgliederliste konnte nicht vollständig geladen werden")
            role = guild.get_role(self.bot.settings.organizer_role_id)
            if role:
                members.update({member.id: member for member in role.members if not member.bot})

        await database.prepare_reminders(survey.id, list(members))
        pending_ids = await database.pending_reminder_ids(survey.id)
        registrations = await database.list_registrations(survey.id)
        marker = "[DEBUG] " if scope == "debug" else ""
        for user_id in pending_ids:
            member = members.get(user_id) or guild.get_member(user_id)
            if member is None:
                await database.record_reminder(survey.id, user_id, now, "Mitglied nicht gefunden")
                continue
            try:
                await member.send(
                    f"{marker}Die Anmeldung für **{survey.month:02d}/{survey.year}** ist beendet. "
                    f"Es liegen **{len(registrations)} Anmeldungen** vor. Bitte plant nun die Gruppen und Termine."
                )
            except discord.HTTPException as exc:
                log.warning("DM an %s fehlgeschlagen: %s", user_id, exc)
                await database.record_reminder(survey.id, user_id, now, str(exc)[:500])
            else:
                await database.record_reminder(survey.id, user_id, now, None)
        counts = await database.reminder_counts(survey.id)
        if counts["pending"] == 0 and counts["failed"] == 0:
            await database.complete_reminders(survey.id, datetime.now(self.bot.settings.timezone))
        return counts

    async def _announcement_channel(self) -> discord.TextChannel:
        channel = self.bot.get_channel(self.bot.settings.announcement_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.bot.settings.announcement_channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("ANNOUNCEMENT_CHANNEL_ID ist kein Textkanal.")
        return channel

    async def _message_exists(self, survey: Survey) -> bool:
        try:
            channel = await self._announcement_channel()
            await channel.fetch_message(survey.message_id)  # type: ignore[arg-type]
            return True
        except discord.NotFound:
            return False

    async def _disable_message(self, survey: Survey, scope: Scope) -> None:
        if survey.message_id is None:
            return
        try:
            channel = await self._announcement_channel()
            message = await channel.fetch_message(survey.message_id)
            await message.edit(embed=self.announcement_embed(survey, scope, closed=True), view=None)
        except (discord.NotFound, discord.Forbidden):
            log.warning("Ankündigung %s konnte nicht deaktiviert werden", survey.message_id)

    async def delete_debug(self, survey: Survey) -> None:
        if survey.message_id:
            try:
                channel = await self._announcement_channel()
                message = await channel.fetch_message(survey.message_id)
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                log.warning("Debug-Ankündigung %s konnte nicht gelöscht werden", survey.message_id)
        await self.bot.debug_db.delete_survey(survey.guild_id, survey.year, survey.month)

    @staticmethod
    def announcement_embed(survey: Survey, scope: Scope, closed: bool = False) -> discord.Embed:
        is_closed = closed or survey.state == "closed"
        debug_prefix = "[DEBUG] " if scope == "debug" else ""
        month_name = german_month(survey.month)
        title = f"{debug_prefix}🧙 Save the Date – Pen & Paper Clubwoche im {month_name}! 🐉"
        if is_closed:
            description = (
                "Die Anmeldung für diese Clubwoche ist beendet. Die Orga plant jetzt "
                "auf Grundlage eurer Angaben die Gruppen und Spieltermine. 🎲"
            )
        else:
            description = (
                f"Im **{month_name}** ist es wieder soweit: Unsere nächste Pen-&-Paper-Clubwoche steht an!\n"
                "Egal ob erfahren oder kompletter Neuling – **alle sind willkommen**. 🎲✨\n\n"
                "Alle Systeme sind gern gesehen – ob D&D, Cthulhu, Indie-Systeme oder etwas ganz anderes."
            )
        embed = discord.Embed(
            title=title,
            description=description,
            color=0xED4245 if is_closed else (0xFEE75C if scope == "debug" else 0xF59E0B),
        )
        embed.add_field(
            name="📅 Clubwoche",
            value=f"**{german_date(survey.week_start)}** bis **{german_date(survey.week_end)}**",
            inline=False,
        )
        embed.add_field(
            name="⏳ Anmeldeschluss",
            value=f"<t:{int(survey.deadline.timestamp())}:F> · <t:{int(survey.deadline.timestamp())}:R>",
            inline=False,
        )
        if not is_closed:
            embed.add_field(
                name="⚔️ Jetzt mitmachen",
                value=(
                    "Melde dich als **Spieler**, **DM** oder für **beides** an und wähle deine verfügbaren Tage.\n"
                    "Deine Angaben kannst du bis zum Anmeldeschluss jederzeit über den Button bearbeiten."
                ),
                inline=False,
            )
            embed.set_footer(text="Die konkrete Gruppen- und Terminplanung erfolgt nach dem Anmeldeschluss.")
        if scope == "debug":
            embed.set_footer(text="DEBUGTEST – getrennte Testdatenbank, aber echte Nachrichten und DMs")
        return embed
