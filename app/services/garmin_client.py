import os, time, logging, asyncio
from datetime import date
from garminconnect import Garmin, GarminConnectTooManyRequestsError
from app.core.config import settings

logger = logging.getLogger(__name__)
TOKEN_DIR = settings.garmintokens  # .garminconnect by default

# Module-level cache so login happens at most once per process.
# In CI the filesystem is fresh each run, so without caching we'd re-login
# every day Strava asks us to enrich — hammering Garmin's rate limit.
_client: Garmin | None = None
_login_failed: bool = False  # if True, skip further attempts this run


def _get_client_sync() -> Garmin:
    global _client, _login_failed
    if _client is not None:
        return _client
    if _login_failed:
        raise RuntimeError("Garmin login previously failed this run")
    if not settings.garmin_email or not settings.garmin_password:
        _login_failed = True
        raise RuntimeError("Garmin credentials not configured")

    os.makedirs(TOKEN_DIR, exist_ok=True)
    client = Garmin(email=settings.garmin_email, password=settings.garmin_password)
    try:
        client.login(TOKEN_DIR)
    except FileNotFoundError:
        logger.info("No cached Garmin tokens; performing fresh SSO login")
        # garminconnect's login() auto-reads GARMINTOKENS env var and tries to
        # load from it. Pop the var so the library takes the credential path.
        saved = os.environ.pop("GARMINTOKENS", None)
        try:
            # Retry login with exponential backoff on rate-limit responses
            max_attempts = 4
            delay = 2.0
            for attempt in range(1, max_attempts + 1):
                try:
                    client.login()
                    break
                except GarminConnectTooManyRequestsError:
                    if attempt < max_attempts:
                        logger.warning(
                            "Garmin login rate-limited (attempt %d/%d). Retrying in %.1fs",
                            attempt, max_attempts, delay,
                        )
                        time.sleep(delay)
                        delay *= 2
                    else:
                        _login_failed = True
                        raise
        finally:
            if saved is not None:
                os.environ["GARMINTOKENS"] = saved
        try:
            client.garth.dump(TOKEN_DIR)
        except Exception as e:
            logger.debug("Could not persist Garmin token: %s", e)
    _client = client
    return client


async def get_client() -> Garmin:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_client_sync)


async def fetch_daily_metrics(
    target_date: date,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> dict:
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

    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        try:
            return await loop.run_in_executor(None, _fetch)
        except GarminConnectTooManyRequestsError:
            if attempt < max_attempts:
                logger.warning(
                    "Garmin fetch rate-limited (attempt %d/%d). Retrying in %.1fs",
                    attempt, max_attempts, delay,
                )
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise
