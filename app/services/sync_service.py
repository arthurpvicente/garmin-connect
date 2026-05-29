import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.strava_client import StravaClient
from app.services.garmin_client import fetch_daily_metrics
from app.services.normalizer import normalize_strava_activity, enrich_with_garmin
from app.services.activity_service import upsert_activities
from app.services.pr_engine import check_and_update_prs
from app.models.activity import Activity

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, db: AsyncSession, strava: StravaClient):
        self.db = db
        self.strava = strava

    async def run(self) -> dict:
        raw_activities = await self.strava.get_activities()
        user_id = self.strava.user_id

        garmin_cache: dict = {}
        enriched: list[dict] = []

        for raw in raw_activities:
            activity_data = normalize_strava_activity(raw, user_id=user_id)
            day = activity_data["start_date"].date()
            if day not in garmin_cache:
                try:
                    garmin_cache[day] = await fetch_daily_metrics(day)
                except Exception as e:
                    logger.warning("Garmin enrichment skipped for %s: %s", day, e)
                    garmin_cache[day] = None
            enriched.append(enrich_with_garmin(activity_data, garmin_cache[day]))

        synced = await upsert_activities(enriched, self.db)

        # Recompute PRs across all activities (idempotent — only updates if better)
        all_acts = (await self.db.execute(
            select(Activity).where(Activity.user_id == user_id)
        )).scalars().all()
        for act in all_acts:
            await check_and_update_prs(user_id, act, self.db)

        # Embed newly synced activities so the assistant can find them.
        # Best-effort: if OPENAI_API_KEY isn't set or the call fails, sync still succeeds.
        try:
            from app.services.embeddings import embed_activities
            embedded = await embed_activities(self.db, user_id, only_missing=True)
            if embedded:
                logger.info("Embedded %d new activities", embedded)
        except Exception as e:
            logger.warning("Embedding skipped (non-fatal): %s", e)

        return {"synced": synced}
