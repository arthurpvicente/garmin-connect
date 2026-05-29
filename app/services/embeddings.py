import logging
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.activity import Activity
from app.models.activity_embedding import ActivityEmbedding
from app.api.analytics import _categorize, _fmt_pace

logger = logging.getLogger(__name__)

# Gemini's REST API endpoint for single-document embedding.
# The SDK uses batchEmbedContents which text-embedding-004 doesn't support,
# so we call embedContent directly via httpx.
_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"

_TASK_TYPE_MAP = {
    "retrieval_document": "RETRIEVAL_DOCUMENT",
    "retrieval_query": "RETRIEVAL_QUERY",
}


def build_activity_summary(activity: Activity) -> str:
    """Convert one Activity row into a compact natural-language sentence.

    This is what gets embedded and stored. It should contain everything
    Gemini might need to answer a question: date, sport, distance, duration,
    pace, heart rate, calories, and Garmin recovery data.
    """
    cat = _categorize(activity.type)
    date_str = activity.start_date.strftime("%A %B %-d, %Y")
    parts = [f"{date_str}: {activity.name} ({cat})"]

    if activity.distance_m:
        parts.append(f"{activity.distance_m / 1000:.2f} km")

    if activity.duration_s:
        mins = activity.duration_s // 60
        h, m = divmod(mins, 60)
        parts.append(f"{h}h {m:02d}m" if h else f"{m} min")

    pace = _fmt_pace(activity.avg_pace_s_km)
    if pace:
        parts.append(f"avg pace {pace}/km")

    if activity.avg_heart_rate:
        parts.append(f"avg HR {int(activity.avg_heart_rate)} bpm")

    if activity.calories:
        parts.append(f"{activity.calories} kcal")

    if activity.elevation_m:
        parts.append(f"{int(activity.elevation_m)} m elevation")

    if activity.suffer_score:
        parts.append(f"suffer score {activity.suffer_score}")

    garmin = []
    if activity.sleep_score_prev_night:
        garmin.append(f"sleep score {activity.sleep_score_prev_night}/100")
    if activity.body_battery_at_start:
        garmin.append(f"body battery {activity.body_battery_at_start}%")
    if activity.hrv_status_on_day:
        garmin.append(f"HRV status {activity.hrv_status_on_day}")
    if garmin:
        parts.append("Garmin: " + ", ".join(garmin))

    return ". ".join(parts) + "."


async def embed_text(text: str, task_type: str = "retrieval_document") -> list[float]:
    """Ask Gemini to turn a string into 768 numbers (an embedding vector).

    task_type controls how Gemini interprets the text:
      "retrieval_document" — use when indexing activity summaries
      "retrieval_query"    — use when embedding the user's question
    Using the right task type gives slightly better similarity results.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured — add it to .env")

    # Strip "models/" prefix if present — .env may carry the old SDK format
    model_name = settings.embedding_model.removeprefix("models/")
    url = _EMBED_URL.format(model=model_name)
    payload = {
        "model": f"models/{model_name}",
        "content": {"parts": [{"text": text}]},
        "taskType": _TASK_TYPE_MAP.get(task_type, "RETRIEVAL_DOCUMENT"),
        "outputDimensionality": 768,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()["embedding"]["values"]


async def embed_activities(
    db: AsyncSession, user_id: uuid.UUID, only_missing: bool = True
) -> int:
    """Embed activities for a user and persist them to activity_embeddings.

    Returns the number of activities newly embedded.
    Set only_missing=False to re-embed everything (e.g. after changing the model).
    """
    if only_missing:
        # LEFT JOIN: give me activities that have NO matching embedding row yet
        stmt = (
            select(Activity)
            .outerjoin(ActivityEmbedding, Activity.id == ActivityEmbedding.activity_id)
            .where(Activity.user_id == user_id)
            .where(ActivityEmbedding.activity_id.is_(None))
        )
    else:
        stmt = select(Activity).where(Activity.user_id == user_id)

    activities = (await db.execute(stmt)).scalars().all()
    count = 0
    for act in activities:
        try:
            summary = build_activity_summary(act)
            vec = await embed_text(summary, task_type="retrieval_document")
            db.add(ActivityEmbedding(
                activity_id=act.id,
                user_id=act.user_id,
                summary=summary,
                embedding=vec,
                model=settings.embedding_model,
            ))
            count += 1
        except Exception as e:
            logger.warning("Embedding skipped for activity %s: %s", act.id, e)
    if count:
        await db.flush()
    return count
