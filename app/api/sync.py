from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.sync_service import SyncService
from app.services.strava_client import StravaClient

router = APIRouter(prefix='/sync', tags=['sync'])

@router.post('')
async def trigger_sync(db: AsyncSession = Depends(get_db)):
    strava = StravaClient(db)  # loads + auto-refreshes token from DB
    service = SyncService(db=db, strava=strava)
    return await service.run()
