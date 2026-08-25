from datetime import date, datetime
from zoneinfo import ZoneInfo

from pnp_bot.models import Survey
from pnp_bot.service import SurveyService


def test_announcement_embed_is_a_friendly_clubweek_invitation() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    survey = Survey(
        id=1, guild_id=2, year=2026, month=8, channel_id=3, message_id=4,
        week_start=date(2026, 8, 24), week_end=date(2026, 8, 30),
        deadline=datetime(2026, 8, 17, 23, 59, tzinfo=timezone), state="open",
        created_at=datetime(2026, 8, 1, 9, tzinfo=timezone), closed_at=None,
        reminder_last_run_at=None, reminder_completed_at=None,
    )

    embed = SurveyService.announcement_embed(survey, "production")

    assert embed.title == "🧙 Save the Date – Pen & Paper Clubwoche im August! 🐉"
    assert "alle sind willkommen" in (embed.description or "")
    assert [field.name for field in embed.fields] == [
        "📅 Clubwoche", "⏳ Anmeldeschluss", "⚔️ Jetzt mitmachen"
    ]
