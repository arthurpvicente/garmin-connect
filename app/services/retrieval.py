import uuid
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.activity_embedding import ActivityEmbedding
from app.services.embeddings import embed_text


async def search_activities(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    k: int = 8,
    sport: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Find the k most relevant activities for a natural-language query.

    Steps:
    1. Embed the query (same 1536-number vector as the stored summaries).
    2. Use pgvector's <=> operator to rank rows by cosine distance.
    3. Join back to activities for the full row.
    4. Return a list of dicts with everything the assistant needs.
    """
    query_vec = await embed_text(query, task_type="retrieval_query")

    distance_col = ActivityEmbedding.embedding.cosine_distance(query_vec).label("distance")

    stmt = (
        select(Activity, ActivityEmbedding.summary, distance_col)
        .join(ActivityEmbedding, Activity.id == ActivityEmbedding.activity_id)
        .where(ActivityEmbedding.user_id == user_id)
    )

    if sport:
        stmt = stmt.where(Activity.type == sport)
    if date_from:
        stmt = stmt.where(Activity.start_date >= date_from)
    if date_to:
        stmt = stmt.where(Activity.start_date <= date_to)

    stmt = stmt.order_by(distance_col).limit(k)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "activity_id": str(act.id),
            "summary": summary,
            "distance": float(distance),
            "name": act.name,
            "type": act.type,
            "start_date": act.start_date.isoformat(),
        }
        for act, summary, distance in rows
    ]
