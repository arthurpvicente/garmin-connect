import json
import logging
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)

# Lazily create the client; if redis_url is empty, all cache ops become no-ops.
redis_client = None
if settings.redis_url:
    try:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning("Redis unavailable, cache disabled: %s", e)


async def cache_get(key: str):
    if redis_client is None:
        return None
    try:
        val = await redis_client.get(key)
        return json.loads(val) if val else None
    except Exception as e:
        logger.debug("cache_get failed: %s", e)
        return None


async def cache_set(key: str, value, ttl: int = 3600):
    if redis_client is None:
        return
    try:
        await redis_client.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.debug("cache_set failed: %s", e)


async def cache_invalidate_user(user_id) -> None:
    if redis_client is None:
        return
    try:
        pattern = f"analytics:{user_id}:*"
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
    except Exception as e:
        logger.debug("cache_invalidate_user failed: %s", e)
