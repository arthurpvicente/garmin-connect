from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func
from app.models.activity import Activity


async def upsert_activities(activities: list[dict], db: AsyncSession) -> int:
    if not activities:
        return 0

    stmt = pg_insert(Activity).values(activities)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "strava_id"],
        set_={
            k: stmt.excluded[k]
            for k in activities[0]
            if k not in ("user_id", "strava_id")
        },
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount
