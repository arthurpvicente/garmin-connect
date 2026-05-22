import os, logging, asyncio
from datetime import date
from garminconnect import Garmin
from app.core.config import settings

logger = logging.getLogger(__name__)
TOKEN_DIR = settings.garmintokens  # .garminconnect by default

def _get_client_sync() -> Garmin:
    os.makedirs(TOKEN_DIR, exist_ok=True)
    client = Garmin(
        email=settings.garmin_email,
        password=settings.garmin_password,
    )
    try:
        client.login(TOKEN_DIR)
    except FileNotFoundError:
        logger.info("No cached Garmin tokens; performing fresh SSO login")
        # garminconnect's login() auto-reads GARMINTOKENS env var and tries to
        # load from it, then re-raises FileNotFoundError without falling back
        # to credentials. Pop the var so the library takes the credential path.
        saved = os.environ.pop("GARMINTOKENS", None)
        try:
            client.login()
        finally:
            if saved is not None:
                os.environ["GARMINTOKENS"] = saved
        client.garth.dump(TOKEN_DIR)
    return client

async def get_client() -> Garmin:
    # Run blocking login in thread pool so FastAPI stays async
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_client_sync)

async def fetch_daily_metrics(target_date: date) -> dict:
    client = await get_client()
    date_str = target_date.isoformat()
    loop = asyncio.get_event_loop()

    def _fetch():
        return {
            'body_battery': client.get_body_battery(date_str, date_str),
            'hrv': client.get_hrv_data(date_str),
            'sleep': client.get_sleep_data(date_str),
            'stress': client.get_stress_data(date_str),
            'steps': client.get_stats(date_str).get('totalSteps'),
        }

    return await loop.run_in_executor(None, _fetch)
