# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the full stack (Postgres + Redis + app with hot-reload)
docker compose up

# Run tests (requires Postgres running locally or via Docker)
pytest tests/
pytest tests/ --cov=app          # with coverage
pytest tests/test_oauth_state.py  # single file
pytest tests/ -k "test_retry"     # single test by name

# Run the API locally without Docker
uvicorn app.main:app --reload

# Embed existing activities into pgvector (run once after first sync)
.venv/bin/python -m scripts.setup_assistant

# Start the MCP server (for Claude Desktop integration)
.venv/bin/python -m mcp_server.server
```

## Environment

Copy `.env.example` to `.env`. Required variables:

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/fitness_sync` |
| `TOKEN_ENCRYPTION_KEY` | Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | From Strava API settings |
| `STRAVA_REDIRECT_URI` | `http://localhost:8000/auth/callback` |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | Optional — Garmin enrichment is skipped if missing |
| `GEMINI_API_KEY` | Optional — required for `/assistant/ask`, embeddings, and MCP server. Free key at aistudio.google.com |
| `ASSISTANT_MODEL` | Default: `gemini-2.5-flash` |
| `EMBEDDING_MODEL` | Default: `gemini-embedding-2` |

`DEBUG=true` makes the app call `create_tables()` on startup (dev convenience). In production, use Alembic migrations instead.

## Architecture

FastAPI app with async SQLAlchemy + asyncpg (Postgres). Three routers mount at startup: `/auth`, `/sync`, and `/assistant`. An APScheduler job runs `auto_sync_job` every 30 minutes in-process.

### Request flow — Strava sync

```
GET /sync/strava?user_id=<uuid>
  → token_service.get_valid_strava_token()   # loads OAuthToken from DB, refreshes if is_expired()
  → strava_client.StravaClient.get_activities()  # paginates all pages (100/page), @with_retry for 429s
  → normalizer.normalize_strava_activity()   # maps raw Strava JSON → Activity kwargs
  → garmin_client.fetch_daily_metrics()      # optional enrichment per day, retries on rate limit
  → normalizer.enrich_with_garmin()
  → activity_service.upsert_activities()     # ON CONFLICT (user_id, strava_id) DO UPDATE
  → embeddings.embed_activities()            # best-effort: embeds new activities into pgvector
```

The scheduler's `auto_sync_job` (in `tasks/scheduler.py`) uses `StravaClient` + `SyncService` directly rather than going through the HTTP endpoint.

### RAG Training Assistant

Three-phase AI layer built on top of the sync service:

**Phase 1 — Embeddings + retrieval** (`app/services/embeddings.py`, `app/services/retrieval.py`)
- `build_activity_summary(activity)` converts each Activity row into a natural-language sentence
- `embed_text(text)` calls the Gemini Embeddings API (768-dim vectors, `gemini-embedding-2`)
- `embed_activities(db, user_id)` finds unembedded activities and stores vectors in `activity_embeddings` (pgvector, HNSW cosine index)
- `search_activities(db, user_id, query, k)` embeds the query and retrieves the k nearest activities by cosine distance

**Phase 2 — LangGraph agent** (`app/services/agent.py`)
- `run_agent(db, user_id, question)` builds a LangGraph graph per request with two tools:
  - `get_week_stats(week_offset)` — wraps `_compute_week_stats` from `analytics.py` for aggregate SQL totals
  - `semantic_search(query, k, sport)` — wraps `retrieval.search_activities` for vector similarity
- The Gemini LLM (`gemini-2.5-flash`) decides which tool(s) to call based on the question, then synthesises a grounded answer
- `app/api/assistant.py` exposes `POST /assistant/ask` (routes through the agent) and `POST /assistant/reindex` (embeds missing activities)

**Phase 3 — MCP server** (`mcp_server/server.py`)
- Standalone process exposing three MCP tools to Claude Desktop and other MCP clients
- Tools reuse the same service layer as the FastAPI app — no duplicated logic
- `get_weekly_stats`, `search_activities`, `ask_assistant`
- `starlette` is pinned to `<0.42.0` in `requirements.txt` — MCP pulls `sse-starlette` which upgrades Starlette to 1.x and breaks FastAPI 0.115.0

### Request flow — Assistant

```
POST /assistant/ask {"question": "..."}
  → run_agent(db, user_id, question)
      → LangGraph agent node (Gemini with tools bound)
      → tool call: get_week_stats(week_offset)  ← if question needs aggregate stats
      → tool call: semantic_search(query)       ← if question needs specific activities
      → agent synthesises answer from tool results
  → AskResponse {answer, cited_activities[]}
```

### OAuth state (CSRF defense)

`app/core/security.py` holds an in-process dict (`_oauth_states`) of short-lived CSRF tokens (5-min TTL). `generate_oauth_state()` mints a token; `verify_oauth_state()` consumes it (one-time use). The `/auth/strava/login` endpoint generates one and `/auth/callback` verifies it before exchanging the code.

**Limitation**: the in-process dict doesn't survive restarts or work across multiple workers. Replace with Redis for production.

### Token storage

`OAuthToken` (in `app/models/token.py`) stores encrypted access/refresh tokens via Fernet (`app/core/security.encrypt_token` / `decrypt_token`). `OAuthToken.is_expired()` includes a 5-minute buffer. The unique constraint is `(user_id, provider)`.

### Retry

`app/core/retry.py` provides `@with_retry(max_attempts, base_delay)` — exponential backoff on `httpx.HTTPStatusError` 429. Applied to `StravaClient.get_activities()`. Garmin uses an inline retry loop (catches `GarminConnectTooManyRequestsError`).

## Testing

Tests use `httpx.AsyncClient` with `ASGITransport` (no live server needed). `conftest.py` sets up and tears down the real Postgres schema once per session (`scope='session', loop_scope='session'`). Tests that don't hit the DB (unit tests for services) use `AsyncMock` for the session.

`pytest.ini` sets `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = session` — both are required; changing either breaks the session-scoped DB fixture.

Assistant endpoint tests (`tests/test_assistant.py`) mock `run_agent` at the endpoint level — CI never calls the real Gemini API. `build_activity_summary` is tested with `SimpleNamespace` objects instead of real SQLAlchemy model instances (SQLAlchemy instrumentation breaks `Activity.__new__`).

## MCP Server — Claude Desktop Setup

Add to `~/.claude/claude_desktop_config.json` (Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "fitness-sync": {
      "command": "/absolute/path/to/fitness-sync/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/fitness-sync"
    }
  }
}
```

Restart Claude Desktop — it will discover the three tools automatically.
