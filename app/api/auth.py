from datetime import datetime, timezone
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.config import settings
from app.core.security import encrypt_token, generate_oauth_state, verify_oauth_state
from app.models.token import OAuthToken
from app.models.user import User
import httpx

router = APIRouter(prefix='/auth', tags=['auth'])

STRAVA_AUTH_URL = 'https://www.strava.com/oauth/authorize'


@router.get('/me')
async def me(db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User))).scalars().first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail='No user connected. Visit /auth/strava/login first.',
        )
    return {'id': str(user.id), 'username': user.username, 'profile_pic': user.profile_pic}


@router.get('/garmin/verify')
async def garmin_verify():
    from app.services.garmin_client import get_client
    try:
        await get_client()
        return {'status': 'connected'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'Garmin login failed: {e}')
STRAVA_TOKEN_URL = 'https://www.strava.com/oauth/token'

@router.get('/strava/login')
async def strava_login():
    state = generate_oauth_state()
    params = {
        'client_id': settings.strava_client_id,
        'redirect_uri': settings.strava_redirect_uri,
        'response_type': 'code',
        'scope': 'read,activity:read_all',
        'state': state,
    }
    url = STRAVA_AUTH_URL + '?' + urlencode(params)
    return RedirectResponse(url=url)

@router.get('/callback')
async def strava_callback(
    code: str,
    state: str,
    scope: str = '',
    db: AsyncSession = Depends(get_db),
):
    if not verify_oauth_state(state):
        raise HTTPException(status_code=400, detail='Invalid or expired OAuth state')

    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            'client_id': settings.strava_client_id,
            'client_secret': settings.strava_client_secret,
            'code': code,
            'grant_type': 'authorization_code',
        })
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f'Strava token exchange failed: {resp.text}')
    data = resp.json()

    athlete = data['athlete']
    strava_id = athlete['id']

    user = (await db.execute(select(User).where(User.strava_id == strava_id))).scalar_one_or_none()
    if user is None:
        user = User(
            strava_id=strava_id,
            username=athlete.get('username') or f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip() or f'strava_{strava_id}',
            profile_pic=athlete.get('profile'),
        )
        db.add(user)
        await db.flush()

    expires_at = datetime.fromtimestamp(data['expires_at'], tz=timezone.utc)
    scopes = [s for s in scope.split(',') if s]

    token = (await db.execute(
        select(OAuthToken).where(
            OAuthToken.user_id == user.id,
            OAuthToken.provider == 'strava',
        )
    )).scalar_one_or_none()

    if token is None:
        token = OAuthToken(
            user_id=user.id,
            provider='strava',
            access_token=encrypt_token(data['access_token']),
            refresh_token=encrypt_token(data['refresh_token']),
            expires_at=expires_at,
            scopes=scopes,
        )
        db.add(token)
    else:
        token.access_token = encrypt_token(data['access_token'])
        token.refresh_token = encrypt_token(data['refresh_token'])
        token.expires_at = expires_at
        token.scopes = scopes

    return RedirectResponse(url='/report?connected=strava', status_code=302)
