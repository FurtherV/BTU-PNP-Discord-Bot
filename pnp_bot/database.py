from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .dates import MonthSchedule
from .models import Registration, Survey


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    channel_id TEXT NOT NULL,
    message_id TEXT,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    deadline TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'closed')),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    reminder_last_run_at TEXT,
    reminder_completed_at TEXT,
    UNIQUE (guild_id, year, month)
);

CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_player INTEGER NOT NULL CHECK (is_player IN (0, 1)),
    is_dm INTEGER NOT NULL CHECK (is_dm IN (0, 1)),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (survey_id, user_id),
    CHECK (is_player = 1 OR is_dm = 1)
);

CREATE TABLE IF NOT EXISTS availability (
    registration_id INTEGER NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
    available_date TEXT NOT NULL,
    PRIMARY KEY (registration_id, available_date)
);

CREATE TABLE IF NOT EXISTS reminder_deliveries (
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    last_attempt_at TEXT,
    delivered_at TEXT,
    error TEXT,
    PRIMARY KEY (survey_id, user_id)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.executescript(SCHEMA)
            cursor = await connection.execute("SELECT COUNT(*) FROM schema_version")
            count = (await cursor.fetchone())[0]
            if count == 0:
                await connection.execute("INSERT INTO schema_version(version) VALUES (1)")
            await connection.commit()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            await connection.close()

    async def get_or_create_survey(
        self, guild_id: int, channel_id: int, schedule: MonthSchedule
    ) -> tuple[Survey, bool]:
        now = datetime.now(schedule.deadline.tzinfo).isoformat()
        async with self.connect() as connection:
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO surveys
                    (guild_id, year, month, channel_id, week_start, week_end, deadline, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id), schedule.year, schedule.month, str(channel_id),
                    schedule.week_start.isoformat(), schedule.week_end.isoformat(),
                    schedule.deadline.isoformat(), now,
                ),
            )
            created = cursor.rowcount == 1
            await connection.commit()
            survey = await self._get_survey(connection, guild_id, schedule.year, schedule.month)
            assert survey is not None
            return survey, created

    async def get_metadata(self, key: str) -> str | None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT value FROM bot_metadata WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        async with self.connect() as connection:
            await connection.execute(
                """INSERT INTO bot_metadata(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )
            await connection.commit()

    async def get_survey(self, guild_id: int, year: int, month: int) -> Survey | None:
        async with self.connect() as connection:
            return await self._get_survey(connection, guild_id, year, month)

    async def get_survey_by_message(self, message_id: int) -> Survey | None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM surveys WHERE message_id = ?", (str(message_id),)
            )
            return self._survey(await cursor.fetchone())

    async def list_open_surveys(self, guild_id: int) -> list[Survey]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                """SELECT * FROM surveys
                   WHERE guild_id = ? AND state = 'open' AND message_id IS NOT NULL
                   ORDER BY year, month""",
                (str(guild_id),),
            )
            surveys = [self._survey(row) for row in await cursor.fetchall()]
            return [survey for survey in surveys if survey is not None]

    async def _get_survey(
        self, connection: aiosqlite.Connection, guild_id: int, year: int, month: int
    ) -> Survey | None:
        cursor = await connection.execute(
            "SELECT * FROM surveys WHERE guild_id = ? AND year = ? AND month = ?",
            (str(guild_id), year, month),
        )
        return self._survey(await cursor.fetchone())

    async def set_message(self, survey_id: int, message_id: int) -> None:
        async with self.connect() as connection:
            await connection.execute(
                "UPDATE surveys SET message_id = ? WHERE id = ?", (str(message_id), survey_id)
            )
            await connection.commit()

    async def close_survey(self, survey_id: int, now: datetime) -> Survey:
        async with self.connect() as connection:
            await connection.execute(
                """UPDATE surveys SET state = 'closed', closed_at = COALESCE(closed_at, ?)
                   WHERE id = ?""",
                (now.isoformat(), survey_id),
            )
            await connection.commit()
            cursor = await connection.execute("SELECT * FROM surveys WHERE id = ?", (survey_id,))
            survey = self._survey(await cursor.fetchone())
            assert survey is not None
            return survey

    async def save_registration(
        self,
        survey_id: int,
        user_id: int,
        display_name: str,
        is_player: bool,
        is_dm: bool,
        notes: str,
        available_dates: tuple[date, ...],
        now: datetime,
    ) -> Registration:
        if not (is_player or is_dm):
            raise ValueError("Mindestens eine Rolle ist erforderlich.")
        if not available_dates:
            raise ValueError("Mindestens ein verfügbarer Tag ist erforderlich.")
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT state, week_start, week_end, deadline FROM surveys WHERE id = ?", (survey_id,)
            )
            survey_row = await cursor.fetchone()
            if survey_row is None:
                raise LookupError("Die Monatsabfrage existiert nicht mehr.")
            deadline = datetime.fromisoformat(survey_row["deadline"])
            if survey_row["state"] != "open" or now > deadline:
                raise PermissionError("Die Anmeldung ist bereits geschlossen.")
            start, end = date.fromisoformat(survey_row["week_start"]), date.fromisoformat(survey_row["week_end"])
            if any(day < start or day > end for day in available_dates):
                raise ValueError("Die Verfügbarkeit enthält einen ungültigen Tag.")

            await connection.execute(
                """
                INSERT INTO registrations
                    (survey_id, user_id, display_name, is_player, is_dm, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(survey_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    is_player = excluded.is_player,
                    is_dm = excluded.is_dm,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (survey_id, str(user_id), display_name[:100], int(is_player), int(is_dm), notes[:1000], now.isoformat(), now.isoformat()),
            )
            cursor = await connection.execute(
                "SELECT id FROM registrations WHERE survey_id = ? AND user_id = ?",
                (survey_id, str(user_id)),
            )
            registration_id = (await cursor.fetchone())["id"]
            await connection.execute("DELETE FROM availability WHERE registration_id = ?", (registration_id,))
            await connection.executemany(
                "INSERT INTO availability(registration_id, available_date) VALUES (?, ?)",
                [(registration_id, day.isoformat()) for day in sorted(set(available_dates))],
            )
            await connection.commit()
        registration = await self.get_registration(survey_id, user_id)
        assert registration is not None
        return registration

    async def get_registration(self, survey_id: int, user_id: int) -> Registration | None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM registrations WHERE survey_id = ? AND user_id = ?",
                (survey_id, str(user_id)),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return await self._registration(connection, row)

    async def list_registrations(self, survey_id: int) -> list[Registration]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM registrations WHERE survey_id = ? ORDER BY display_name COLLATE NOCASE",
                (survey_id,),
            )
            return [await self._registration(connection, row) for row in await cursor.fetchall()]

    async def delete_registration(self, survey_id: int, user_id: int, now: datetime) -> bool:
        async with self.connect() as connection:
            cursor = await connection.execute("SELECT state, deadline FROM surveys WHERE id = ?", (survey_id,))
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("Die Monatsabfrage existiert nicht mehr.")
            if row["state"] != "open" or now > datetime.fromisoformat(row["deadline"]):
                raise PermissionError("Die Anmeldung ist bereits geschlossen.")
            cursor = await connection.execute(
                "DELETE FROM registrations WHERE survey_id = ? AND user_id = ?",
                (survey_id, str(user_id)),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def prepare_reminders(self, survey_id: int, user_ids: list[int]) -> None:
        async with self.connect() as connection:
            await connection.executemany(
                """INSERT OR IGNORE INTO reminder_deliveries(survey_id, user_id, status)
                   VALUES (?, ?, 'pending')""",
                [(survey_id, str(user_id)) for user_id in user_ids],
            )
            await connection.commit()

    async def begin_reminder_run(self, survey_id: int, now: datetime) -> None:
        async with self.connect() as connection:
            await connection.execute(
                "UPDATE surveys SET reminder_last_run_at = ? WHERE id = ?",
                (now.isoformat(), survey_id),
            )
            await connection.commit()

    async def complete_reminders(self, survey_id: int, now: datetime) -> None:
        async with self.connect() as connection:
            await connection.execute(
                "UPDATE surveys SET reminder_completed_at = ? WHERE id = ?",
                (now.isoformat(), survey_id),
            )
            await connection.commit()

    async def pending_reminder_ids(self, survey_id: int) -> list[int]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT user_id FROM reminder_deliveries WHERE survey_id = ? AND status != 'delivered'",
                (survey_id,),
            )
            return [int(row["user_id"]) for row in await cursor.fetchall()]

    async def record_reminder(self, survey_id: int, user_id: int, now: datetime, error: str | None) -> None:
        async with self.connect() as connection:
            await connection.execute(
                """UPDATE reminder_deliveries
                   SET status = ?, last_attempt_at = ?, delivered_at = ?, error = ?
                   WHERE survey_id = ? AND user_id = ?""",
                (
                    "failed" if error else "delivered", now.isoformat(),
                    None if error else now.isoformat(), error, survey_id, str(user_id),
                ),
            )
            await connection.commit()

    async def reminder_counts(self, survey_id: int) -> dict[str, int]:
        result = {"pending": 0, "delivered": 0, "failed": 0}
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT status, COUNT(*) AS amount FROM reminder_deliveries WHERE survey_id = ? GROUP BY status",
                (survey_id,),
            )
            for row in await cursor.fetchall():
                result[row["status"]] = row["amount"]
        return result

    async def delete_survey(self, guild_id: int, year: int, month: int) -> Survey | None:
        async with self.connect() as connection:
            survey = await self._get_survey(connection, guild_id, year, month)
            if survey is not None:
                await connection.execute("DELETE FROM surveys WHERE id = ?", (survey.id,))
                await connection.commit()
            return survey

    async def _registration(self, connection: aiosqlite.Connection, row: aiosqlite.Row) -> Registration:
        cursor = await connection.execute(
            "SELECT available_date FROM availability WHERE registration_id = ? ORDER BY available_date",
            (row["id"],),
        )
        days = tuple(date.fromisoformat(item["available_date"]) for item in await cursor.fetchall())
        return Registration(
            id=row["id"], survey_id=row["survey_id"], user_id=int(row["user_id"]),
            display_name=row["display_name"], is_player=bool(row["is_player"]),
            is_dm=bool(row["is_dm"]), notes=row["notes"], available_dates=days,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _survey(row: aiosqlite.Row | None) -> Survey | None:
        if row is None:
            return None
        return Survey(
            id=row["id"], guild_id=int(row["guild_id"]), year=row["year"], month=row["month"],
            channel_id=int(row["channel_id"]), message_id=int(row["message_id"]) if row["message_id"] else None,
            week_start=date.fromisoformat(row["week_start"]), week_end=date.fromisoformat(row["week_end"]),
            deadline=datetime.fromisoformat(row["deadline"]), state=row["state"],
            created_at=datetime.fromisoformat(row["created_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            reminder_last_run_at=datetime.fromisoformat(row["reminder_last_run_at"]) if row["reminder_last_run_at"] else None,
            reminder_completed_at=datetime.fromisoformat(row["reminder_completed_at"]) if row["reminder_completed_at"] else None,
        )
