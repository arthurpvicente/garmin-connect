import uuid
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.retrieval import search_activities

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Gemini REST API endpoint for text generation.
_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """\
You are a personal fitness training assistant for a single athlete. Your role \
is to analyze their training history and give specific, data-backed answers.

Rules you must follow:
1. Ground every claim in the activity data supplied in the user message. \
Do not use general knowledge about typical athletes or invent numbers.
2. If the provided data is insufficient to answer the question, say so clearly \
and explain what data would be needed.
3. Cite specific activities by their date and name when making a point \
(e.g. "on your Wednesday run (May 28) your pace was 5'30\"/km").
4. Be quantitative: mention pace, heart rate, distance, duration where relevant.
5. Highlight trends when you can: improving pace, rising HR, volume ramp-up, \
recovery patterns.
6. Keep answers to 2-4 paragraphs unless the question clearly needs more.
7. Use a supportive, coaching tone — not clinical, not overly casual.
"""


async def _generate_answer(user_message: str) -> str:
    """Call Gemini's generateContent REST endpoint and return the response text."""
    url = _GENERATE_URL.format(model=settings.assistant_model)
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            params={"key": settings.gemini_api_key},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


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
    k: int = 8


class AskResponse(BaseModel):
    answer: str
    cited_activities: list[str]


@router.post("/ask", response_model=AskResponse)
async def ask_assistant(body: AskRequest, db: AsyncSession = Depends(get_db)):
    """Answer a natural-language question about your training, grounded in your data."""
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

    # Step 1: find the most relevant activities via vector similarity
    hits = await search_activities(db, user_id, body.question, k=body.k)
    if not hits:
        return AskResponse(
            answer=(
                "I couldn't find any embedded activities to answer your question. "
                "Try running POST /assistant/reindex first to embed your activities."
            ),
            cited_activities=[],
        )

    # Step 2: build the context block Gemini will read
    context = "\n\n".join(
        f"[{i + 1}] {h['summary']}" for i, h in enumerate(hits)
    )
    user_message = (
        f"Here are your most relevant training activities:\n\n"
        f"{context}\n\n"
        f"Question: {body.question}"
    )

    # Step 3: call Gemini and return the grounded answer
    answer = await _generate_answer(user_message)

    return AskResponse(
        answer=answer,
        cited_activities=[h["activity_id"] for h in hits],
    )


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
