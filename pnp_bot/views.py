from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Awaitable, Callable, Literal

import discord

from .database import Database
from .dates import german_date
from .models import Registration, Survey

Scope = Literal["production", "debug"]
log = logging.getLogger(__name__)


async def report_view_error(interaction: discord.Interaction, error: Exception) -> None:
    log.error(
        "Discord-Komponente fehlgeschlagen",
        exc_info=(type(error), error, error.__traceback__),
    )
    message = "Der Dialog konnte nicht geöffnet werden. Details stehen im Bot-Log."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        log.exception("Fehlermeldung zur Discord-Komponente konnte nicht gesendet werden")


def role_text(registration: Registration) -> str:
    if registration.is_player and registration.is_dm:
        return "Spieler & DM"
    return "Spieler" if registration.is_player else "DM"


class NotesModal(discord.ui.Modal, title="Anmerkung"):
    notes = discord.ui.TextInput(
        label="Optionale Anmerkung",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
        placeholder="Wünsche, Einschränkungen oder weitere Hinweise",
    )

    def __init__(self, wizard: "RegistrationWizard"):
        super().__init__()
        self.wizard = wizard
        self.notes.default = wizard.notes

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.wizard.notes = str(self.notes.value).strip()
        await interaction.response.send_message(
            "Anmerkung übernommen. Kehre zum Anmeldedialog zurück und speichere dort.", ephemeral=True
        )


class RoleSelect(discord.ui.Select):
    def __init__(self, wizard: "RegistrationWizard", registration: Registration | None):
        selected = set()
        if registration and registration.is_player:
            selected.add("player")
        if registration and registration.is_dm:
            selected.add("dm")
        options = [
            discord.SelectOption(label="Spieler", value="player", default="player" in selected),
            discord.SelectOption(label="DM", value="dm", default="dm" in selected),
        ]
        super().__init__(placeholder="Rolle(n) auswählen", min_values=1, max_values=2, options=options, row=0)
        self.wizard = wizard

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wizard.roles = set(self.values)
        await interaction.response.defer()


class DateSelect(discord.ui.Select):
    def __init__(self, wizard: "RegistrationWizard", survey: Survey, registration: Registration | None):
        selected = set(registration.available_dates if registration else ())
        options = []
        current = survey.week_start
        while current <= survey.week_end:
            options.append(discord.SelectOption(label=german_date(current), value=current.isoformat(), default=current in selected))
            current = current.fromordinal(current.toordinal() + 1)
        super().__init__(placeholder="Verfügbare Tage auswählen", min_values=1, max_values=7, options=options, row=1)
        self.wizard = wizard

    async def callback(self, interaction: discord.Interaction) -> None:
        self.wizard.available_dates = {date.fromisoformat(value) for value in self.values}
        await interaction.response.defer()


class RegistrationWizard(discord.ui.View):
    def __init__(
        self,
        database: Database,
        survey: Survey,
        user: discord.Member | discord.User,
        registration: Registration | None,
        timezone,
    ):
        super().__init__(timeout=600)
        self.database = database
        self.survey = survey
        self.user = user
        self.timezone = timezone
        self.roles: set[str] = set()
        if registration:
            if registration.is_player:
                self.roles.add("player")
            if registration.is_dm:
                self.roles.add("dm")
        self.available_dates = set(registration.available_dates if registration else ())
        self.notes = registration.notes if registration else ""
        self.add_item(RoleSelect(self, registration))
        self.add_item(DateSelect(self, survey, registration))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Dieser Dialog gehört einem anderen Mitglied.", ephemeral=True)
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_view_error(interaction, error)

    @discord.ui.button(label="Anmerkung", style=discord.ButtonStyle.secondary, row=2)
    async def edit_notes(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(NotesModal(self))

    @discord.ui.button(label="Anmeldung speichern", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.roles or not self.available_dates:
            await interaction.response.send_message("Bitte wähle mindestens eine Rolle und einen verfügbaren Tag.", ephemeral=True)
            return
        try:
            registration = await self.database.save_registration(
                self.survey.id, self.user.id, self.user.display_name,
                "player" in self.roles, "dm" in self.roles, self.notes,
                tuple(sorted(self.available_dates)), datetime.now(self.timezone),
            )
        except (ValueError, LookupError, PermissionError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        days = ", ".join(german_date(day) for day in registration.available_dates)
        await interaction.response.edit_message(
            content=f"Anmeldung gespeichert: **{role_text(registration)}** – verfügbar: {days}", view=None
        )


class ConfirmView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        action: Callable[[discord.Interaction], Awaitable[None]],
        confirm_label: str = "Bestätigen",
        danger: bool = True,
    ):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.action = action
        self.confirm.label = confirm_label
        self.confirm.style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.success

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Diese Bestätigung gehört einem anderen Mitglied.", ephemeral=True)
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_view_error(interaction, error)

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await self.action(interaction)
        self.stop()

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Aktion abgebrochen.", view=None)
        self.stop()


class SignupPersistentView(discord.ui.View):
    def __init__(self, bot, scope: Scope):
        super().__init__(timeout=None)
        self.bot = bot
        self.scope = scope

    async def _survey(self, interaction: discord.Interaction) -> Survey | None:
        if interaction.message is None:
            return None
        return await self.bot.service.database(self.scope).get_survey_by_message(interaction.message.id)

    async def handle_signup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        survey = await self._survey(interaction)
        if survey is None:
            await interaction.followup.send("Diese Monatsabfrage wurde nicht gefunden.", ephemeral=True)
            return
        await open_registration(interaction, self.bot.service.database(self.scope), survey, self.bot.settings.timezone)

    async def handle_unregister(self, interaction: discord.Interaction) -> None:
        survey = await self._survey(interaction)
        if survey is None:
            await interaction.response.send_message("Diese Monatsabfrage wurde nicht gefunden.", ephemeral=True)
            return
        database = self.bot.service.database(self.scope)
        if survey.state != "open" or datetime.now(self.bot.settings.timezone) > survey.deadline:
            await interaction.response.send_message("Die Anmeldung ist bereits geschlossen.", ephemeral=True)
            return
        registration = await database.get_registration(survey.id, interaction.user.id)
        if registration is None:
            await interaction.response.send_message(
                "Du hast für diese Clubwoche keine Anmeldung gespeichert.", ephemeral=True
            )
            return

        async def action(confirm_interaction: discord.Interaction) -> None:
            try:
                deleted = await database.delete_registration(
                    survey.id, interaction.user.id, datetime.now(self.bot.settings.timezone)
                )
                text = "Deine Anmeldung wurde gelöscht." if deleted else "Für dich war keine Anmeldung gespeichert."
            except (LookupError, PermissionError) as exc:
                text = str(exc)
            await confirm_interaction.response.edit_message(content=text, view=None)

        await interaction.response.send_message(
            "Möchtest du deine Anmeldung wirklich löschen?", ephemeral=True,
            view=ConfirmView(interaction.user.id, action, "Abmelden"),
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_view_error(interaction, error)


class ProductionSignupView(SignupPersistentView):
    def __init__(self, bot):
        super().__init__(bot, "production")

    @discord.ui.button(
        label="Anmelden / Bearbeiten",
        style=discord.ButtonStyle.primary,
        custom_id="pnp:production:signup",
    )
    async def signup_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.handle_signup(interaction)

    @discord.ui.button(
        label="Abmelden",
        style=discord.ButtonStyle.secondary,
        custom_id="pnp:production:unregister",
    )
    async def unregister_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.handle_unregister(interaction)


class DebugSignupView(SignupPersistentView):
    def __init__(self, bot):
        super().__init__(bot, "debug")

    @discord.ui.button(
        label="Anmelden / Bearbeiten",
        style=discord.ButtonStyle.primary,
        custom_id="pnp:debug:signup",
    )
    async def signup_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.handle_signup(interaction)

    @discord.ui.button(
        label="Abmelden",
        style=discord.ButtonStyle.secondary,
        custom_id="pnp:debug:unregister",
    )
    async def unregister_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await self.handle_unregister(interaction)


async def open_registration(
    interaction: discord.Interaction, database: Database, survey: Survey, timezone
) -> None:
    now = datetime.now(timezone)
    if survey.state != "open" or now > survey.deadline:
        if interaction.response.is_done():
            await interaction.followup.send("Die Anmeldung ist bereits geschlossen.", ephemeral=True)
        else:
            await interaction.response.send_message("Die Anmeldung ist bereits geschlossen.", ephemeral=True)
        return
    registration = await database.get_registration(survey.id, interaction.user.id)
    view = RegistrationWizard(database, survey, interaction.user, registration, timezone)
    content = (
        f"Anmeldung für **{survey.month:02d}/{survey.year}**. "
        "Wähle Rolle und Tage; eine bestehende Antwort ist vorausgewählt."
    )
    if interaction.response.is_done():
        await interaction.followup.send(content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)


class Paginator(discord.ui.View):
    def __init__(self, owner_id: int, pages: list[discord.Embed]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.pages = pages
        self.index = 0
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Diese Ansicht gehört einem anderen Admin.", ephemeral=True)
            return False
        return True

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_view_error(interaction, error)

    @discord.ui.button(label="Zurück", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class ButtonProbeView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dieser Test gehört einem anderen Mitglied.", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="Button testen",
        style=discord.ButtonStyle.success,
        custom_id="pnp:debug:button_probe",
    )
    async def probe(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="✅ Button-Interaktion wurde erfolgreich empfangen.", view=None
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_view_error(interaction, error)
