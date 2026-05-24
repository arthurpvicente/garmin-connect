import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.activity import Activity
from app.models.personal_record import PersonalRecord

PR_METRICS: dict[str, callable] = {
    "longest_run":       lambda a: a.distance_m,
    "highest_elevation": lambda a: a.elevation_m,
    "longest_duration":  lambda a: a.duration_s,
    # lower pace = faster → negate so higher value = better record
    "best_avg_pace":     lambda a: -a.avg_pace_s_km if a.avg_pace_s_km else None,
}


async def check_and_update_prs(
    user_id: uuid.UUID, activity: Activity, db: AsyncSession
) -> list[str]:
    """Check activity against stored PRs; upsert any new records. Returns list of beaten metrics."""
    beaten = []
    for metric, fn in PR_METRICS.items():
        value = fn(activity)
        if value is None:
            continue

        existing = (await db.execute(
            select(PersonalRecord).where(
                PersonalRecord.user_id == user_id,
                PersonalRecord.metric == metric,
            )
        )).scalar_one_or_none()

        if existing is None or value > existing.value:
            # Use the activity's actual date — not "now" — so historical
            # syncs don't make every old PR look like it happened today.
            achieved = activity.start_date
            stmt = pg_insert(PersonalRecord).values(
                user_id=user_id,
                metric=metric,
                value=value,
                activity_id=activity.id,
                achieved_at=achieved,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "metric"],
                set_={"value": value, "activity_id": activity.id, "achieved_at": achieved},
            )
            await db.execute(stmt)
            beaten.append(metric)

    return beaten
