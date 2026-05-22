import asyncio
import functools
import logging
from httpx import HTTPStatusError

logger = logging.getLogger(__name__)


def with_retry(max_attempts: int = 5, base_delay: float = 1.0):
    """Retry an async function on 429 rate-limit responses with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except HTTPStatusError as e:
                    if e.response.status_code == 429 and attempt < max_attempts:
                        logger.warning(
                            "Rate limited on attempt %d/%d. Retrying in %.1fs...",
                            attempt, max_attempts, delay,
                        )
                        await asyncio.sleep(delay)
                        delay *= 2
                    else:
                        raise
        return wrapper
    return decorator
