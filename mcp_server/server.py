"""
MCP server for fitness-sync.

Exposes three tools to any MCP client (e.g. Claude Desktop):
  - get_weekly_stats   — aggregate training totals for any week
  - search_activities  — semantic search over embedded activities
  - ask_assistant      — full LangGraph agent answer grounded in real data

Run with:
    .venv/bin/python -m mcp_server.server

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "fitness-sync": {
          "command": "/absolute/path/to/.venv/bin/python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "/absolute/path/to/fitness-sync"
        }
      }
    }
"""

import asyncio
import json
import uuid

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.api.analytics import _compute_week_stats
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.user import User
from app.services.agent import run_agent
from app.services.retrieval import search_activities as _search_activities

mcp = FastMCP("fitness-sync")


async def _first_user_id() -> uuid.UUID:
    """Return the first user in the DB — the single-athlete default."""
    async with AsyncSessionFactory() as db:
        user = (await db.execute(select(User))).scalars().first()
    if not user:
        raise RuntimeError("No user found. Complete the Strava OAuth flow first.")
    return user.id


@mcp.tool()
async def get_weekly_stats(week_offset: int = 0) -> str:
    """Get aggregate training statistics for a specific week.

    Returns totals for running, cycling, strength, sleep, and recovery.
    week_offset: 0 = current week, -1 = last week, -2 = two weeks ago, etc.
    """
    user_id = await _first_user_id()
    async with AsyncSessionFactory() as db:
        stats = await _compute_week_stats(user_id, week_offset, db)
    return json.dumps(stats, default=str)


@mcp.tool()
async def search_activities(
    query: str,
    k: int = 8,
    sport: str | None = None,
) -> str:
    """Search for activities semantically similar to a query.

    Embeds the query with Gemini and finds the closest activities via pgvector.
    Returns activity summaries with distances (lower = more relevant).

    query: Natural-language description of the activities you want.
    k: Number of results (default 8).
    sport: Optional Strava type filter — e.g. "Run", "Ride", "Walk", "WeightTraining".
    """
    if not settings.gemini_api_key:
        return json.dumps({"error": "GEMINI_API_KEY not set — cannot embed query."})
    user_id = await _first_user_id()
    async with AsyncSessionFactory() as db:
        hits = await _search_activities(db, user_id, query, k=k, sport=sport)
    return json.dumps(hits, default=str)


@mcp.tool()
async def ask_assistant(question: str) -> str:
    """Ask a natural-language question about your training history.

    The LangGraph agent decides whether to use aggregate week stats,
    semantic activity search, or both, then returns a grounded answer.

    question: Any training question, e.g. "How has my pace trended this month?"
    """
    if not settings.gemini_api_key:
        return json.dumps({
            "error": "GEMINI_API_KEY not set. Add it to .env to use the assistant."
        })
    user_id = await _first_user_id()
    async with AsyncSessionFactory() as db:
        answer, cited = await run_agent(db, user_id, question)
    return json.dumps({"answer": answer, "cited_activities": cited})


if __name__ == "__main__":
    mcp.run()
