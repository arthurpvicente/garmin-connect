import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.agent import run_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])


async def _default_user_id(db: AsyncSession) -> uuid.UUID:
    user = (await db.execute(select(User))).scalars().first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="No user found. Complete the Strava OAuth flow first.",
        )
    return user.id


class AskRequest(BaseModel):
    question: str
    user_id: uuid.UUID | None = None


class AskResponse(BaseModel):
    answer: str
    cited_activities: list[str]


@router.post("/ask", response_model=AskResponse)
async def ask_assistant(body: AskRequest, db: AsyncSession = Depends(get_db)):
    """Answer a natural-language question about your training.

    The agent decides whether to use aggregate week stats, semantic activity search,
    or both — then synthesises a grounded answer from the results.
    """
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Assistant not configured. "
                "Set GEMINI_API_KEY in your .env file "
                "(get a free key at aistudio.google.com)."
            ),
        )

    user_id = body.user_id or await _default_user_id(db)
    answer, cited = await run_agent(db, user_id, body.question)
    return AskResponse(answer=answer, cited_activities=cited)


@router.post("/reindex")
async def reindex(
    user_id: uuid.UUID | None = None,
    full: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Embed all activities that don't have an embedding yet.

    Set full=true to re-embed everything (useful after changing EMBEDDING_MODEL).
    This calls the Gemini Embeddings API once per activity.
    """
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY not set — cannot generate embeddings.",
        )
    from app.services.embeddings import embed_activities

    resolved_user_id = user_id or await _default_user_id(db)
    count = await embed_activities(db, resolved_user_id, only_missing=not full)
    return {"embedded": count}
