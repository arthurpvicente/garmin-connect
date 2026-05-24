import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.models.activity import Activity

router = APIRouter(prefix='/activities', tags=['activities'])

RUN_TYPES = {'Run', 'Walk', 'Hike', 'Ride', 'VirtualRide', 'EBikeRide', 'Swim'}


@router.get('')
async def list_activities(
    user_id: uuid.UUID,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.start_date.desc())
        .limit(limit)
    )
    activities = result.scalars().all()
    return [_shape(a) for a in activities]


def _shape(a: Activity) -> dict:
    activity_type = 'run' if a.type in RUN_TYPES else 'strength'
    return {
        'id': str(a.id),
        'strava_id': a.strava_id,
        'type': activity_type,
        'strava_type': a.type,
        'name': a.name,
        'start_date': a.start_date.isoformat(),
        'distance_m': a.distance_m,
        'duration_s': a.duration_s,
        'avg_heart_rate': a.avg_heart_rate,
        'max_heart_rate': a.max_heart_rate,
        'elevation_m': a.elevation_m,
        'avg_pace_s_km': a.avg_pace_s_km,
        'avg_speed_ms': a.avg_speed_ms,
        'calories': a.calories,
        'suffer_score': a.suffer_score,
        'kudos_count': a.kudos_count,
        'platform': 'strava',
    }
