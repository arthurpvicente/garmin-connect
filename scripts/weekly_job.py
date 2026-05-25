"""
Standalone weekly job — runs once per week from GitHub Actions.

Does what the in-process scheduler used to do:
  1. Pull fresh activities from Strava
  2. Compute this week's report
  3. Send WhatsApp via Twilio

Reads all config from env vars (same names as the local .env file).
Exits non-zero on critical failure so the GitHub Action shows red.
"""
import asyncio
import logging
import sys
from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.user import User
from app.services.strava_client import StravaClient
from app.services.sync_service import SyncService
from app.services.whatsapp import send_message, format_weekly_report
from app.api.analytics import _compute_week_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("weekly_job")


async def main() -> int:
    async with AsyncSessionFactory() as db:
        # 1. Find the user
        user = (await db.execute(select(User))).scalars().first()
        if not user:
            log.error("No user in DB. Run Strava OAuth locally first.")
            return 2
        log.info("User: %s (%s)", user.username, user.id)

        # 2. Sync Strava (best-effort; Garmin enrichment may fail in CI)
        try:
            strava = StravaClient(db)
            result = await SyncService(db=db, strava=strava).run()
            await db.commit()
            log.info("Strava sync: %s", result)
        except Exception as e:
            log.warning("Strava sync failed (will still try to send report): %s", e)

        # 3. Compute this week's stats
        try:
            current = await _compute_week_stats(user.id, week_offset=0, db=db)
            previous = await _compute_week_stats(user.id, week_offset=-1, db=db)
            # Build the same comparison block /analytics/weekly-report adds
            def _delta(c, p):
                if c is None or p is None: return None
                return round(c - p, 1) if isinstance(c, float) else c - p
            current["comparison"] = {
                "running_km_delta": _delta(current["running"]["total_km"], previous["running"]["total_km"]),
                "running_sessions_delta": current["running"]["sessions"] - previous["running"]["sessions"],
                "walking_km_delta": _delta(current["walking"]["total_km"], previous["walking"]["total_km"]),
                "strength_sessions_delta": current["strength"]["sessions"] - previous["strength"]["sessions"],
                "active_days_delta": current["totals"]["active_days"] - previous["totals"]["active_days"],
                "total_activities_delta": current["totals"]["total_activities"] - previous["totals"]["total_activities"],
                "total_calories_delta": current["totals"]["total_calories"] - previous["totals"]["total_calories"],
                "sleep_score_delta": _delta(current["sleep"]["avg_score"], previous["sleep"]["avg_score"]),
                "body_battery_delta": _delta(current["recovery"]["avg_body_battery"], previous["recovery"]["avg_body_battery"]),
            }
        except Exception as e:
            log.error("Failed to build report: %s", e)
            return 3

        # 4. Send WhatsApp
        text = format_weekly_report(current)
        ok = await send_message(text)
        if not ok:
            log.error("WhatsApp send failed — check Twilio creds + sandbox join")
            return 4

        log.info("Weekly WhatsApp report sent successfully")
        return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
