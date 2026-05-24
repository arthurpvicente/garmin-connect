import uuid
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.cache import cache_invalidate_user
from app.services.strava_client import StravaClient
from app.services.sync_service import SyncService
from app.models.token import OAuthToken

router = APIRouter(prefix='/sync', tags=['sync'])


@router.get('/strava')
async def sync_strava(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    strava = StravaClient(db)
    service = SyncService(db=db, strava=strava)
    result = await service.run()
    await db.commit()
    await cache_invalidate_user(user_id)
    return result


@router.get('/platform-status')
async def platform_status(db: AsyncSession = Depends(get_db)):
    tokens = (await db.execute(select(OAuthToken))).scalars().all()
    result = {}
    for t in tokens:
        updated = t.updated_at.isoformat() if t.updated_at else None
        result[t.provider] = {
            'connected': True,
            'expires_at': t.expires_at.isoformat(),
            'is_expired': t.is_expired(),
            'scopes': t.scopes,
            'updated_at': updated,
        }
    return result
