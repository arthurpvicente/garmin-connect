import logging
from datetime import datetime, timezone
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
from app.core.retry import with_retry
from app.models.token import OAuthToken

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


class StravaClient:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._token: OAuthToken | None = None

    @property
    def user_id(self):
        if self._token is None:
            raise RuntimeError("StravaClient.user_id accessed before token was loaded")
        return self._token.user_id

    async def _load_token(self) -> OAuthToken:
        if self._token is not None:
            return self._token
        stmt = (
            select(OAuthToken)
            .where(OAuthToken.provider == "strava")
            .order_by(OAuthToken.updated_at.desc())
            .limit(1)
        )
        token = (await self.db.execute(stmt)).scalar_one_or_none()
        if token is None:
            raise HTTPException(
                status_code=401,
                detail="No Strava OAuth token found. Connect via /auth/strava/login first.",
            )
        self._token = token
        return token

    async def _refresh(self, token: OAuthToken) -> str:
        refresh_plain = decrypt_token(token.refresh_token)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                STRAVA_TOKEN_URL,
                data={
                    "client_id": settings.strava_client_id,
                    "client_secret": settings.strava_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_plain,
                },
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail=f"Strava token refresh failed: {resp.text}",
            )
        data = resp.json()
        token.access_token = encrypt_token(data["access_token"])
        token.refresh_token = encrypt_token(data["refresh_token"])
        token.expires_at = datetime.fromtimestamp(data["expires_at"], tz=timezone.utc)
        await self.db.flush()
        logger.info("Refreshed Strava token for user_id=%s", token.user_id)
        return data["access_token"]

    async def _get_valid_access_token(self) -> str:
        token = await self._load_token()
        if token.is_expired():
            return await self._refresh(token)
        return decrypt_token(token.access_token)

    @with_retry(max_attempts=5, base_delay=1.0)
    async def get_activities(self, limit: int = 100) -> list[dict]:
        access_token = await self._get_valid_access_token()
        all_activities: list[dict] = []
        page = 1
        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{STRAVA_API_BASE}/athlete/activities",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"per_page": limit, "page": page},
                )
                if resp.status_code == 401:
                    raise HTTPException(status_code=401, detail="Strava rejected the access token")
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                all_activities.extend(batch)
                page += 1
        return all_activities
