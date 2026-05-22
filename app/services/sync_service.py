import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import func
from app.services.strava_client import StravaClient
from app.services.garmin_client import fetch_daily_metrics
from app.services.normalizer import normalize_strava_activity, enrich_with_garmin
from app.models.activity import Activity

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, db: AsyncSession, strava: StravaClient):
        self.db = db
        self.strava = strava

    async def run(self) -> dict:
        raw_activities = await self.strava.get_activities(limit=30)
        user_id = self.strava.user_id

        # Cache Garmin daily metrics per date — many activities share a day.
        garmin_cache: dict = {}

        synced = 0
        for raw in raw_activities:
            activity_data = normalize_strava_activity(raw, user_id=user_id)
            day = activity_data["start_date"].date()
            if day not in garmin_cache:
                try:
                    garmin_cache[day] = await fetch_daily_metrics(day)
                except Exception as e:
                    logger.warning("Garmin enrichment skipped for %s: %s", day, e)
                    garmin_cache[day] = None
            activity_data = enrich_with_garmin(activity_data, garmin_cache[day])

            update_set = {
                k: v
                for k, v in activity_data.items()
                if k not in ("user_id", "strava_id")
            }
            update_set["synced_at"] = func.now()

            stmt = insert(Activity).values(**activity_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "strava_id"],
                set_=update_set,
            )
            await self.db.execute(stmt)
            synced += 1

        return {"synced": synced}
