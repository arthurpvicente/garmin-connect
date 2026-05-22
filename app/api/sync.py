import uuid
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.services.token_service import get_valid_strava_token

router = APIRouter(prefix='/sync', tags=['sync'])

STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


@router.get('/strava')
async def sync_strava(
    user_id: uuid.UUID,       # replace with real auth later
    db: AsyncSession = Depends(get_db),
):
    access_token = await get_valid_strava_token(user_id, db)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            STRAVA_ACTIVITIES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"per_page": 30, "page": 1},
        )
        response.raise_for_status()
        activities = response.json()

    # normalize and save activities — future part
    return {"synced": len(activities)}
