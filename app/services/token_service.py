import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.token import OAuthToken
from app.core.security import encrypt_token, decrypt_token
from app.core.config import settings


async def get_valid_strava_token(user_id, db: AsyncSession) -> str:
    """
    Return a valid Strava access token for the user.
    Refreshes automatically if the token is expired or expiring soon.
    """
    result = await db.execute(
        select(OAuthToken).where(
            OAuthToken.user_id == user_id,
            OAuthToken.provider == "strava"
        )
    )
    token_row = result.scalar_one_or_none()

    if token_row is None:
        raise ValueError("No Strava token found for this user")

    if token_row.is_expired():
        token_row = await _refresh_strava_token(token_row, db)

    return decrypt_token(token_row.access_token)


async def _refresh_strava_token(token_row: OAuthToken, db: AsyncSession) -> OAuthToken:
    """Call Strava token endpoint to exchange refresh token for new tokens."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": decrypt_token(token_row.refresh_token),
            }
        )
        response.raise_for_status()
        data = response.json()

    # Update the stored tokens with the new values
    token_row.access_token = encrypt_token(data['access_token'])
    token_row.refresh_token = encrypt_token(data['refresh_token'])
    token_row.expires_at = data['expires_at']
    await db.commit()
    await db.refresh(token_row)

    return token_row
