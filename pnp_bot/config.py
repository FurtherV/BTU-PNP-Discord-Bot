from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    pass


def _required_int(name: str) -> int:
    raw = os.getenv(name)
    if not raw:
        raise ConfigurationError(f"{name} fehlt.")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} muss eine Discord-ID sein.") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} muss positiv sein.")
    return value


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    guild_id: int
    announcement_channel_id: int
    organizer_role_id: int
    timezone: ZoneInfo
    database_path: Path
    debug_database_path: Path
    debug_enabled: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ConfigurationError("DISCORD_TOKEN fehlt.")

        timezone_name = os.getenv("TIMEZONE", "Europe/Berlin")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Unbekannte Zeitzone: {timezone_name}") from exc

        database_path = Path(os.getenv("DATABASE_PATH", "data/registrations.sqlite3")).resolve()
        debug_database_path = Path(
            os.getenv("DEBUG_DATABASE_PATH", "data/debug-registrations.sqlite3")
        ).resolve()
        if database_path == debug_database_path:
            raise ConfigurationError("Produktions- und Debugdatenbank müssen getrennt sein.")

        return cls(
            token=token,
            guild_id=_required_int("DISCORD_GUILD_ID"),
            announcement_channel_id=_required_int("ANNOUNCEMENT_CHANNEL_ID"),
            organizer_role_id=_required_int("ORGANIZER_ROLE_ID"),
            timezone=timezone,
            database_path=database_path,
            debug_database_path=debug_database_path,
            debug_enabled=_bool("DEBUG_ENABLED"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
