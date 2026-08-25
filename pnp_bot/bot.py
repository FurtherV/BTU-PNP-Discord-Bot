from __future__ import annotations

import logging
import hashlib
import json
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from .config import ConfigurationError, Settings
from .database import Database
from .dates import schedule_for_month
from .models import Survey
from .service import SurveyService
from .views import DebugSignupView, ProductionSignupView

log = logging.getLogger(__name__)


class RegistrationBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.production_db = Database(settings.database_path)
        self.debug_db = Database(settings.debug_database_path)
        self.service = SurveyService(self)
        self.signup_views = {
            "production": ProductionSignupView(self),
            "debug": DebugSignupView(self),
        }

    async def setup_hook(self) -> None:
        self.tree.on_error = self.on_tree_error
        await self.production_db.initialize()
        await self.debug_db.initialize()
        self.add_view(self.signup_views["production"])
        if self.settings.debug_enabled:
            self.add_view(self.signup_views["debug"])
        restored, missing, unavailable = await self._restore_message_views()
        if restored:
            log.info("%s bestehende Button-Views anhand ihrer Message-ID wiederhergestellt", restored)
        else:
            log.info("Keine bestehenden Button-Views wiederhergestellt")
        if missing:
            log.info(
                "%s gespeicherte Ankündigung(en) nicht mehr vorhanden; "
                "der Scheduler erstellt bei Bedarf Ersatz",
                missing,
            )
        if unavailable:
            log.warning(
                "%s gespeicherte Ankündigung(en) konnten beim Start nicht geprüft werden",
                unavailable,
            )
        await self.load_extension("pnp_bot.commands")
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self._sync_commands_if_changed(guild)
        self.scheduler.start()

    def create_signup_view(self, scope: str):
        if scope == "production":
            return ProductionSignupView(self)
        if scope == "debug":
            return DebugSignupView(self)
        raise ValueError(f"Unbekannter View-Scope: {scope}")

    async def _restore_message_views(self) -> tuple[int, int, int]:
        restored = 0
        missing = 0
        unavailable = 0
        production_surveys = await self.production_db.list_open_surveys(self.settings.guild_id)
        for survey in production_surveys:
            message_status = await self._survey_message_exists(survey)
            if survey.message_id is not None and message_status is True:
                self.add_view(
                    self.create_signup_view("production"),
                    message_id=survey.message_id,
                )
                restored += 1
            elif survey.message_id is not None and message_status is False:
                missing += 1
            elif survey.message_id is not None:
                unavailable += 1

        if self.settings.debug_enabled:
            debug_surveys = await self.debug_db.list_open_surveys(self.settings.guild_id)
            for survey in debug_surveys:
                message_status = await self._survey_message_exists(survey)
                if survey.message_id is not None and message_status is True:
                    self.add_view(
                        self.create_signup_view("debug"),
                        message_id=survey.message_id,
                    )
                    restored += 1
                elif survey.message_id is not None and message_status is False:
                    missing += 1
                elif survey.message_id is not None:
                    unavailable += 1
        return restored, missing, unavailable

    async def _survey_message_exists(self, survey: Survey) -> bool | None:
        try:
            channel = await self.fetch_channel(survey.channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.warning(
                    "Gespeicherter Kanal %s für Monatsabfrage %s/%s ist kein Textkanal",
                    survey.channel_id,
                    survey.month,
                    survey.year,
                )
                return False
            await channel.fetch_message(survey.message_id)
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException:
            log.exception(
                "Gespeicherte Ankündigung %s konnte beim Start nicht geprüft werden",
                survey.message_id,
            )
            return None

    async def _sync_commands_if_changed(self, guild: discord.Object) -> None:
        payload = [
            command.to_dict(self.tree)
            for command in self.tree.get_commands(guild=guild)
        ]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        schema_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        application_key = self.user.id if self.user else "unknown"
        metadata_key = f"command_schema_hash:{application_key}:{guild.id}"
        previous_hash = await self.production_db.get_metadata(metadata_key)
        if previous_hash == schema_hash:
            log.info("Slash-Commands unverändert; Discord-Synchronisierung übersprungen")
            return

        synced = await self.tree.sync(guild=guild)
        await self.production_db.set_metadata(metadata_key, schema_hash)
        log.info(
            "%s Slash-Commands für Guild %s synchronisiert",
            len(synced),
            self.settings.guild_id,
        )

    async def on_tree_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        log.error(
            "Slash-Command fehlgeschlagen",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "Der Befehl konnte nicht ausgeführt werden. Details stehen im Bot-Log."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def close(self) -> None:
        self.scheduler.cancel()
        await super().close()

    @tasks.loop(minutes=1)
    async def scheduler(self) -> None:
        now = datetime.now(self.settings.timezone)
        schedule = schedule_for_month(now.year, now.month, self.settings.timezone)
        try:
            survey = await self.production_db.get_survey(self.settings.guild_id, now.year, now.month)
            if schedule.announcement_at <= now <= schedule.deadline:
                survey, _result = await self.service.start(now.year, now.month, "production")
            if survey is not None and now > survey.deadline:
                retry_due = (
                    survey.reminder_completed_at is None
                    and (
                        survey.reminder_last_run_at is None
                        or now - survey.reminder_last_run_at >= timedelta(hours=6)
                    )
                )
                if survey.state == "open" or retry_due:
                    await self.service.close(survey, "production")
        except Exception:
            log.exception("Automatische Monatsplanung fehlgeschlagen; nächster Versuch folgt")

    @scheduler.before_loop
    async def before_scheduler(self) -> None:
        await self.wait_until_ready()

    async def on_ready(self) -> None:
        log.info("Angemeldet als %s (%s)", self.user, self.user.id if self.user else "unbekannt")

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id") if interaction.data else None
        command_name = interaction.data.get("name") if interaction.data else None
        log.info(
            "Interaktion empfangen: type=%s command=%s custom_id=%s user=%s channel=%s",
            interaction.type.name,
            command_name,
            custom_id,
            interaction.user.id,
            interaction.channel_id,
        )


def run() -> None:
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(f"Konfigurationsfehler: {exc}") from exc
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    RegistrationBot(settings).run(settings.token, log_handler=None)
