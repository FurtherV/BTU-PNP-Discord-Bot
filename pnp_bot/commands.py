from __future__ import annotations

from datetime import datetime
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from .dates import german_date, parse_month
from .export import create_workbook
from .models import Registration, Survey
from .views import ButtonProbeView, ConfirmView, Paginator, open_registration, role_text

Mode = Literal["produktion", "debug"]


async def send_paginated_response(
    interaction: discord.Interaction, pages: list[discord.Embed]
) -> None:
    if len(pages) > 1:
        await interaction.response.send_message(
            embed=pages[0],
            view=Paginator(interaction.user.id, pages),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(embed=pages[0], ephemeral=True)


class RegistrationCommands(commands.Cog):
    anmeldungen = app_commands.Group(name="anmeldungen", description="Anmeldungen auswerten")
    monatsabfrage = app_commands.Group(name="monatsabfrage", description="Produktive Monatsplanung verwalten")
    debug = app_commands.Group(name="debug", description="Isolierte Entwicklungs- und Testbefehle")

    def __init__(self, bot):
        self.bot = bot

    def _now(self) -> datetime:
        return datetime.now(self.bot.settings.timezone)

    def _database(self, mode: Mode):
        return self.bot.debug_db if mode == "debug" else self.bot.production_db

    async def _admin(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        allowed = isinstance(member, discord.Member) and (
            member.guild_permissions.administrator
            or any(role.id == self.bot.settings.organizer_role_id for role in member.roles)
        )
        if not allowed:
            await interaction.response.send_message("Dafür benötigst du die konfigurierte Orga-Rolle.", ephemeral=True)
        return allowed

    async def _debug_admin(self, interaction: discord.Interaction) -> bool:
        if not self.bot.settings.debug_enabled:
            await interaction.response.send_message("Der Debugmodus ist deaktiviert.", ephemeral=True)
            return False
        return await self._admin(interaction)

    async def _survey(self, interaction: discord.Interaction, month: str | None, mode: Mode) -> Survey | None:
        try:
            year, number = parse_month(month, self._now())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return None
        survey = await self._database(mode).get_survey(self.bot.settings.guild_id, year, number)
        if survey is None:
            await interaction.response.send_message(
                f"Für {number:02d}/{year} gibt es keine {'Debug-' if mode == 'debug' else ''}Monatsabfrage.",
                ephemeral=True,
            )
        return survey

    @app_commands.command(name="anmelden", description="Für die aktuelle Eventwoche anmelden oder Antwort bearbeiten")
    @app_commands.guild_only()
    async def signup(self, interaction: discord.Interaction) -> None:
        now = self._now()
        survey = await self.bot.production_db.get_survey(self.bot.settings.guild_id, now.year, now.month)
        if survey is None:
            await interaction.response.send_message("Für diesen Monat gibt es noch keine Anmeldung.", ephemeral=True)
            return
        await open_registration(interaction, self.bot.production_db, survey, self.bot.settings.timezone)

    @app_commands.command(name="abmelden", description="Eigene Anmeldung für den aktuellen Monat löschen")
    @app_commands.guild_only()
    async def unregister(self, interaction: discord.Interaction) -> None:
        now = self._now()
        survey = await self.bot.production_db.get_survey(self.bot.settings.guild_id, now.year, now.month)
        if survey is None:
            await interaction.response.send_message("Für diesen Monat gibt es noch keine Anmeldung.", ephemeral=True)
            return
        if survey.state != "open" or now > survey.deadline:
            await interaction.response.send_message("Die Anmeldung ist bereits geschlossen.", ephemeral=True)
            return
        registration = await self.bot.production_db.get_registration(survey.id, interaction.user.id)
        if registration is None:
            await interaction.response.send_message(
                "Du hast für diese Clubwoche keine Anmeldung gespeichert.", ephemeral=True
            )
            return

        async def action(confirm_interaction: discord.Interaction) -> None:
            try:
                deleted = await self.bot.production_db.delete_registration(survey.id, interaction.user.id, self._now())
                text = "Deine Anmeldung wurde gelöscht." if deleted else "Für dich war keine Anmeldung gespeichert."
            except (LookupError, PermissionError) as exc:
                text = str(exc)
            await confirm_interaction.response.edit_message(content=text, view=None)

        await interaction.response.send_message(
            "Möchtest du deine Anmeldung wirklich löschen?", ephemeral=True,
            view=ConfirmView(interaction.user.id, action, "Abmelden"),
        )

    @anmeldungen.command(name="uebersicht", description="Matrix aller Anmeldungen anzeigen")
    @app_commands.describe(monat="Optional im Format YYYY-MM", modus="Produktions- oder Debugdaten")
    async def overview(self, interaction: discord.Interaction, monat: str | None = None, modus: Mode = "produktion") -> None:
        if not await self._admin(interaction):
            return
        if modus == "debug" and not self.bot.settings.debug_enabled:
            await interaction.response.send_message("Der Debugmodus ist deaktiviert.", ephemeral=True)
            return
        survey = await self._survey(interaction, monat, modus)
        if survey is None:
            return
        registrations = await self._database(modus).list_registrations(survey.id)
        pages = self._overview_pages(survey, registrations, modus)
        await send_paginated_response(interaction, pages)

    @anmeldungen.command(name="einzeln", description="Einzelne Antworten anzeigen")
    @app_commands.describe(monat="Optional im Format YYYY-MM", mitglied="Optional ein bestimmtes Mitglied", modus="Produktions- oder Debugdaten")
    async def details(
        self, interaction: discord.Interaction, monat: str | None = None,
        mitglied: discord.Member | None = None, modus: Mode = "produktion",
    ) -> None:
        if not await self._admin(interaction):
            return
        if modus == "debug" and not self.bot.settings.debug_enabled:
            await interaction.response.send_message("Der Debugmodus ist deaktiviert.", ephemeral=True)
            return
        survey = await self._survey(interaction, monat, modus)
        if survey is None:
            return
        registrations = await self._database(modus).list_registrations(survey.id)
        if mitglied:
            registrations = [item for item in registrations if item.user_id == mitglied.id]
        pages = self._detail_pages(survey, registrations, modus)
        await send_paginated_response(interaction, pages)

    @anmeldungen.command(name="export", description="Anmeldungen als Excel-Datei herunterladen")
    @app_commands.describe(monat="Optional im Format YYYY-MM", modus="Produktions- oder Debugdaten")
    async def export(self, interaction: discord.Interaction, monat: str | None = None, modus: Mode = "produktion") -> None:
        if not await self._admin(interaction):
            return
        if modus == "debug" and not self.bot.settings.debug_enabled:
            await interaction.response.send_message("Der Debugmodus ist deaktiviert.", ephemeral=True)
            return
        survey = await self._survey(interaction, monat, modus)
        if survey is None:
            return
        await interaction.response.defer(ephemeral=True)
        registrations = await self._database(modus).list_registrations(survey.id)
        output = create_workbook(survey, registrations)
        filename = f"anmeldungen-{survey.year}-{survey.month:02d}-{modus}.xlsx"
        await interaction.followup.send(file=discord.File(output, filename=filename), ephemeral=True)

    @monatsabfrage.command(name="planungsstart", description="Produktive Monatsplanung manuell starten oder reparieren")
    @app_commands.describe(monat="Optional im Format YYYY-MM")
    async def production_start(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._admin(interaction):
            return
        try:
            year, number = parse_month(monat, self._now())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        survey, result = await self.bot.service.start(
            year, number, "production", refresh_existing=True
        )
        messages = {
            "created": "Monatsabfrage wurde veröffentlicht.",
            "replaced": "Der fehlende Post wurde ersetzt; bestehende Anmeldungen blieben erhalten.",
            "existing": "Die Monatsabfrage und ihr Post existieren bereits; es wurde nichts dupliziert.",
            "closed": "Die Monatsabfrage ist bereits geschlossen und wurde nicht verändert.",
        }
        await interaction.edit_original_response(content=messages[result])

    @monatsabfrage.command(name="planungsende", description="Produktive Monatsplanung manuell schließen und DMs senden")
    @app_commands.describe(monat="Optional im Format YYYY-MM")
    async def production_end(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._admin(interaction):
            return
        survey = await self._survey(interaction, monat, "produktion")
        if survey is None:
            return
        await self._confirm_close(interaction, survey, "production")

    @monatsabfrage.command(name="status", description="Status einer produktiven Monatsplanung prüfen")
    @app_commands.describe(monat="Optional im Format YYYY-MM")
    async def production_status(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._admin(interaction):
            return
        survey = await self._survey(interaction, monat, "produktion")
        if survey is None:
            return
        await self._send_status(interaction, survey, "produktion")

    @debug.command(name="planungsstart", description="Isolierte Debugplanung sofort starten")
    async def debug_start(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._debug_admin(interaction):
            return
        try:
            year, number = parse_month(monat, self._now())
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        _survey, result = await self.bot.service.start(
            year, number, "debug", refresh_existing=True
        )
        await interaction.edit_original_response(content=f"Debug-Planungsstart: **{result}**.")

    @debug.command(name="planungsende", description="Debugplanung sofort schließen und echte Test-DMs senden")
    async def debug_end(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._debug_admin(interaction):
            return
        survey = await self._survey(interaction, monat, "debug")
        if survey is None:
            return
        await self._confirm_close(interaction, survey, "debug")

    @debug.command(name="status", description="Status einer isolierten Debugplanung prüfen")
    async def debug_status(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._debug_admin(interaction):
            return
        survey = await self._survey(interaction, monat, "debug")
        if survey is None:
            return
        await self._send_status(interaction, survey, "debug")

    @debug.command(name="diagnose", description="Discord-Verbindung, Rechte und Buttons diagnostizieren")
    async def debug_diagnose(self, interaction: discord.Interaction) -> None:
        if not await self._debug_admin(interaction):
            return
        guild = interaction.guild
        if guild is None or guild.me is None:
            await interaction.response.send_message("Guild- oder Botmitglieddaten fehlen.", ephemeral=True)
            return
        channel = guild.get_channel(self.bot.settings.announcement_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Der konfigurierte Ankündigungskanal wurde nicht gefunden.", ephemeral=True)
            return

        permissions = channel.permissions_for(guild.me)
        now = self._now()
        survey = await self.bot.production_db.get_survey(self.bot.settings.guild_id, now.year, now.month)
        message_status = "kein Produktionspost gespeichert"
        message_components: list[str] = []
        if survey and survey.message_id:
            try:
                message = await channel.fetch_message(survey.message_id)
                for row in message.components:
                    children = getattr(row, "children", ())
                    message_components.extend(
                        str(getattr(child, "custom_id", "–")) for child in children
                    )
                message_status = (
                    f"gefunden · ID `{message.id}` · Autor `{message.author.id}` · "
                    f"richtiger Bot: {'ja' if self.bot.user and message.author.id == self.bot.user.id else 'nein'}"
                )
            except discord.HTTPException as exc:
                message_status = f"nicht abrufbar: `{type(exc).__name__} {exc}`"

        registered_components = [
            str(item.custom_id)
            for view in self.bot.persistent_views
            for item in view.children
            if getattr(item, "custom_id", None)
        ]
        await interaction.response.send_message(
            "**Botdiagnose**\n"
            f"Bot-ID: `{self.bot.user.id if self.bot.user else '–'}`\n"
            f"Kanal: `{channel.id}` · Typ: `{channel.type}`\n"
            f"Rechte: view={permissions.view_channel}, send={permissions.send_messages}, "
            f"history={permissions.read_message_history}, commands={permissions.use_application_commands}\n"
            f"Produktionspost: {message_status}\n"
            f"Post-Buttons: `{', '.join(message_components) or 'keine'}`\n"
            f"Registrierte Buttons: `{', '.join(registered_components) or 'keine'}`",
            ephemeral=True,
        )

    @debug.command(name="buttontest", description="Eine minimale Discord-Buttoninteraktion testen")
    async def debug_button_test(self, interaction: discord.Interaction) -> None:
        if not await self._debug_admin(interaction):
            return
        await interaction.response.send_message(
            "Dieser Test verwendet weder SQLite noch den Monatsworkflow.",
            view=ButtonProbeView(interaction.user.id),
            ephemeral=True,
        )

    @debug.command(name="zuruecksetzen", description="Debugplanung und ihre Testdaten entfernen")
    async def debug_reset(self, interaction: discord.Interaction, monat: str | None = None) -> None:
        if not await self._debug_admin(interaction):
            return
        survey = await self._survey(interaction, monat, "debug")
        if survey is None:
            return

        async def action(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.defer()
            await self.bot.service.delete_debug(survey)
            await confirm_interaction.edit_original_response(content="Debugplanung und Testdaten wurden entfernt.", view=None)

        await interaction.response.send_message(
            f"Debugplanung **{survey.month:02d}/{survey.year}** samt Testanmeldungen löschen?",
            ephemeral=True, view=ConfirmView(interaction.user.id, action, "Debugdaten löschen"),
        )

    async def _confirm_close(self, interaction: discord.Interaction, survey: Survey, scope: Literal["production", "debug"]) -> None:
        database = self.bot.service.database(scope)
        registrations = await database.list_registrations(survey.id)
        guild = interaction.guild
        role = guild.get_role(self.bot.settings.organizer_role_id) if guild else None
        recipient_count = len([member for member in role.members if not member.bot]) if role else 0
        warning = "\n⚠️ Die reguläre Frist ist noch nicht erreicht." if scope == "production" and self._now() <= survey.deadline else ""
        marker = "Debug-" if scope == "debug" else ""

        async def action(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.defer()
            try:
                counts = await self.bot.service.close(survey, scope)
                content = (
                    f"{marker}Planung geschlossen. DMs: {counts['delivered']} zugestellt, "
                    f"{counts['failed']} fehlgeschlagen, {counts['pending']} ausstehend."
                )
            except Exception as exc:
                content = f"Planung wurde geschlossen, aber die Erinnerung ist fehlgeschlagen: {exc}"
            await confirm_interaction.edit_original_response(content=content, view=None)

        await interaction.response.send_message(
            f"**{marker}Planungsende {survey.month:02d}/{survey.year}**\n"
            f"Anmeldungen: {len(registrations)} · sichtbare DM-Empfänger: {recipient_count}\n"
            f"Reguläre Frist: <t:{int(survey.deadline.timestamp())}:F>{warning}\n"
            "Diese Aktion schließt die Anmeldung und sendet echte DMs.",
            ephemeral=True,
            view=ConfirmView(interaction.user.id, action, "Planung beenden"),
        )

    async def _send_status(self, interaction: discord.Interaction, survey: Survey, mode: Mode) -> None:
        database = self._database(mode)
        registrations = await database.list_registrations(survey.id)
        counts = await database.reminder_counts(survey.id)
        post_exists = bool(survey.message_id and await self.bot.service._message_exists(survey))
        await interaction.response.send_message(
            f"**{'Debug · ' if mode == 'debug' else ''}{survey.month:02d}/{survey.year}**\n"
            f"Status: `{survey.state}` · Post erreichbar: {'ja' if post_exists else 'nein'}\n"
            f"Anmeldungen: {len(registrations)} · Frist: <t:{int(survey.deadline.timestamp())}:F>\n"
            f"DMs: {counts['delivered']} zugestellt, {counts['failed']} fehlgeschlagen, {counts['pending']} ausstehend",
            ephemeral=True,
        )

    @staticmethod
    def _overview_pages(survey: Survey, registrations: list[Registration], mode: Mode) -> list[discord.Embed]:
        chunks = [registrations[i:i + 10] for i in range(0, len(registrations), 10)] or [[]]
        pages = []
        dates = [survey.week_start.fromordinal(survey.week_start.toordinal() + i) for i in range(7)]
        for index, chunk in enumerate(chunks, 1):
            lines = ["`Name                 Rolle       Mo Di Mi Do Fr Sa So`"]
            for item in chunk:
                marks = ["✓" if day in item.available_dates else "·" for day in dates]
                name = item.display_name[:20]
                lines.append(f"`{name:<20} {role_text(item)[:10]:<10} {'  '.join(marks)}`")
            embed = discord.Embed(
                title=f"{'[DEBUG] ' if mode == 'debug' else ''}Übersicht {survey.month:02d}/{survey.year}",
                description="\n".join(lines), color=0x5865F2,
            )
            embed.set_footer(text=f"Seite {index}/{len(chunks)} · {len(registrations)} Anmeldungen")
            pages.append(embed)
        return pages

    @staticmethod
    def _detail_pages(survey: Survey, registrations: list[Registration], mode: Mode) -> list[discord.Embed]:
        if not registrations:
            return [discord.Embed(title="Keine Antworten", description="Für diese Auswahl wurden keine Anmeldungen gefunden.")]
        pages = []
        for index, item in enumerate(registrations, 1):
            embed = discord.Embed(
                title=f"{'[DEBUG] ' if mode == 'debug' else ''}{item.display_name}",
                color=0x5865F2,
            )
            embed.add_field(name="Discord-ID", value=str(item.user_id), inline=False)
            embed.add_field(name="Rolle", value=role_text(item), inline=False)
            embed.add_field(name="Verfügbar", value="\n".join(german_date(day) for day in item.available_dates), inline=False)
            embed.add_field(name="Anmerkung", value=item.notes or "–", inline=False)
            embed.set_footer(text=f"Antwort {index}/{len(registrations)} · geändert {item.updated_at:%d.%m.%Y %H:%M}")
            pages.append(embed)
        return pages


async def setup(bot) -> None:
    await bot.add_cog(RegistrationCommands(bot))
