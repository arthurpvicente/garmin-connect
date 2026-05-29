# Fitness Sync

A personal fitness dashboard that syncs your Strava activities, enriches them with Garmin recovery metrics, and lets you interrogate your training history through a RAG-powered AI assistant — via a browser chat UI, a REST API, or directly from Claude Desktop through an MCP server.

## Overview

Fitness Sync bridges the gap between fragmented fitness data: your activity history lives in Strava while your recovery data lives in Garmin Connect. This service pulls both into a single Postgres database, builds a vector index over every activity, and exposes that index to a LangGraph agent backed by Gemini.

**What it does:**

- Authenticates with Strava via OAuth 2.0 and continuously syncs your full activity history (runs, rides, swims, strength sessions, etc.).
- Enriches each activity with the Garmin metrics recorded on the same day: body battery, HRV status, and previous night's sleep score.
- Tracks personal records across four metrics (longest run, highest elevation, longest duration, best average pace) and updates them on every sync.
- Embeds every activity into a 768-dimensional pgvector index using the Gemini Embeddings API so they can be retrieved by semantic similarity.
- Exposes a LangGraph agent at `POST /assistant/ask` that routes questions to aggregate SQL stats, semantic vector search, or both — then synthesises a grounded answer.
- Serves a browser chat UI at `/chat` and a formatted weekly report at `/report`.
- Provides an MCP server so Claude Desktop can call `get_weekly_stats`, `search_activities`, and `ask_assistant` as native tools.
- Runs an in-process APScheduler job every 30 minutes and a GitHub Actions cron every Saturday to keep data current.

## Tech Stack

| Concern | Library / Tool | Version |
|---|---|---|
| Language / runtime | Python | 3.12 |
| Web framework | FastAPI | 0.115.0 |
| ASGI server | Uvicorn (with standard extras) | 0.30.6 |
| ORM | SQLAlchemy async | 2.0.36 |
| Database driver | asyncpg | 0.30.0 |
| Database | PostgreSQL | 16 (Docker image) |
| Vector extension | pgvector | 0.4.2 |
| Settings | pydantic-settings | 2.5.2 |
| HTTP client | httpx | 0.27.2 |
| AI LLM | Google Gemini (`gemini-2.5-flash`) | via langchain-google-genai ≥2.0.0 |
| Embeddings | Gemini Embeddings API (`gemini-embedding-2`, 768-dim) | via httpx |
| Agent framework | LangGraph | ≥0.2.0 |
| MCP server | mcp (FastMCP) | ≥1.0.0 |
| Garmin integration | garminconnect | 0.2.38 |
| Token encryption | cryptography (Fernet) | 43.0.3 |
| In-process scheduler | APScheduler (AsyncIOScheduler) | 3.10.4 |
| Cache | Redis (redis[hiredis]) | 5.1.1 (optional) |
| PDF generation | fpdf2 | 2.7.9 |
| Testing | pytest + pytest-asyncio | 8.3.3 / 0.24.0 |
| Containerisation | Docker Compose | — |

> **Starlette pin:** `starlette` is pinned to `>=0.37.2,<0.42.0` in `requirements.txt`. The `mcp` package pulls in `sse-starlette`, which upgrades Starlette to 1.x and breaks FastAPI 0.115.0. Do not remove this constraint.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI app  (app/main.py)                                     │
│                                                                 │
│  /auth      router                                              │
│  /sync      router                                              │
│  /analytics router                                              │
│  /activities router                                             │
│  /assistant router (/ask, /reindex)                             │
│  /chat, /report, /report/pdf, /report/whatsapp                  │
│  /health, /                                                     │
│                                                                 │
│  APScheduler (every 30 min) → SyncService + embed_activities()  │
└─────────────────────────────────────────────────────────────────┘
         │             │              │             │
    Postgres DB    pgvector       Gemini API    Strava / Garmin
    (activities,   (activity_     (embeddings,  (OAuth, activity
     oauth_tokens,  embeddings,    LLM answers)  data, recovery)
     users, PRs)    HNSW index)

┌────────────────────────────────────────────────┐
│  MCP server  (mcp_server/server.py)            │
│  Standalone process — reuses app service layer │
│  Tools: get_weekly_stats, search_activities,   │
│         ask_assistant                          │
└────────────────────────────────────────────────┘
         │
   Claude Desktop (or any MCP client)
```

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

The scheduler's `auto_sync_job` (`app/tasks/scheduler.py`) uses `StravaClient` + `SyncService` directly rather than going through the HTTP endpoint.

### RAG Training Assistant — three-phase pipeline

**Phase 1 — Embeddings** (`app/services/embeddings.py`)

`build_activity_summary(activity)` converts each `Activity` row into a compact natural-language sentence containing date, sport, distance, duration, pace, heart rate, calories, and Garmin recovery fields. `embed_text(text)` calls the Gemini Embeddings REST API directly via httpx (768-dim vectors, `RETRIEVAL_DOCUMENT` task type). `embed_activities(db, user_id)` finds activities without an existing embedding and stores them in the `activity_embeddings` table. New activities are embedded automatically at the end of every sync.

**Phase 2 — Retrieval** (`app/services/retrieval.py`)

`search_activities(db, user_id, query, k, sport)` embeds the query string (`RETRIEVAL_QUERY` task type) and uses pgvector's `<=>` cosine-distance operator to rank the `activity_embeddings` rows. Supports optional Strava type and date-range filters. Returns a list of dicts containing `activity_id`, `summary`, `distance`, `name`, `type`, and `start_date`.

**Phase 3 — LangGraph agent** (`app/services/agent.py`)

`run_agent(db, user_id, question)` builds a fresh LangGraph graph per request with two tools scoped to the requesting user:

| Tool | Backed by | When used |
|---|---|---|
| `get_week_stats(week_offset)` | `_compute_week_stats` from `analytics.py` | Aggregate totals, volume, sleep, recovery trends |
| `semantic_search(query, k, sport)` | `retrieval.search_activities` | Finding specific workouts or pace comparisons |

The `gemini-2.5-flash` LLM decides which tool(s) to call, then synthesises a grounded answer. The agent loop continues until no more tool calls are requested.

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

### OAuth state / CSRF defense

`app/core/security.py` maintains an in-memory dict (`_oauth_states`) mapping 32-byte URL-safe tokens to expiry timestamps (5-minute TTL). `generate_oauth_state()` mints a token; `verify_oauth_state()` consumes it with `pop()` (one-time use). The in-memory dict does not survive restarts and does not work across multiple workers — replace with Redis for multi-instance deployments.

### Token storage and encryption

`OAuthToken` (`app/models/token.py`) stores access and refresh tokens encrypted with Fernet symmetric encryption. The unique constraint is `(user_id, provider)`, so reconnecting replaces the existing token row.

### Retry strategy

`app/core/retry.py` provides `@with_retry(max_attempts, base_delay)` — exponential backoff on `httpx.HTTPStatusError` 429 (default: 5 attempts, 1 s base). Applied to `StravaClient.get_activities()`. Garmin uses an inline retry loop catching `GarminConnectTooManyRequestsError`. Garmin enrichment is always best-effort: if credentials are missing or the API is unreachable, activities are saved with null Garmin fields and the sync continues.

## Project Structure

```
fitness-sync/
├── app/
│   ├── main.py              # FastAPI app, lifespan, router mounts, /chat, /report, /health
│   ├── api/
│   │   ├── auth.py          # /auth router: Strava OAuth, Garmin verify
│   │   ├── sync.py          # /sync router: trigger sync, platform status
│   │   ├── analytics.py     # /analytics router: weekly-report + _compute_week_stats helper
│   │   ├── activities.py    # /activities router: list recent activities
│   │   └── assistant.py     # /assistant router: /ask (LangGraph agent), /reindex
│   ├── core/
│   │   ├── config.py        # pydantic-settings Settings (Gemini keys, models, all env vars)
│   │   ├── database.py      # async engine, session factory, Base, create_tables
│   │   ├── security.py      # Fernet encrypt/decrypt, OAuth state CSRF tokens
│   │   ├── retry.py         # @with_retry decorator (exponential backoff on 429)
│   │   └── cache.py         # Redis async client, no-op if REDIS_URL is empty
│   ├── models/
│   │   ├── user.py          # User table (strava_id, username, profile_pic, max_hr)
│   │   ├── token.py         # OAuthToken table (encrypted access/refresh, is_expired())
│   │   ├── activity.py      # Activity table (Strava fields + Garmin enrichment + raw JSONB)
│   │   ├── activity_embedding.py  # ActivityEmbedding table (Vector(768), HNSW cosine index)
│   │   └── personal_record.py    # PersonalRecord table (one row per user+metric)
│   ├── services/
│   │   ├── strava_client.py    # StravaClient: load/refresh token, paginate activities
│   │   ├── garmin_client.py    # Module-level cached Garmin client, fetch_daily_metrics
│   │   ├── normalizer.py       # normalize_strava_activity, enrich_with_garmin
│   │   ├── activity_service.py # upsert_activities (INSERT … ON CONFLICT DO UPDATE)
│   │   ├── sync_service.py     # SyncService.run() orchestrates the full sync pipeline
│   │   ├── embeddings.py       # build_activity_summary, embed_text, embed_activities
│   │   ├── retrieval.py        # search_activities (pgvector cosine similarity)
│   │   ├── agent.py            # run_agent: LangGraph graph + tool definitions
│   │   ├── pr_engine.py        # check_and_update_prs across 4 metrics
│   │   ├── whatsapp.py         # Twilio send_message, format_weekly_report
│   │   └── pdf_report.py       # generate_weekly_pdf (fpdf2)
│   ├── tasks/
│   │   └── scheduler.py     # APScheduler setup, auto_sync_job (every 30 min)
│   └── static/
│       ├── chat.html        # Browser chat UI served at /chat
│       ├── report.html      # Browser weekly report SPA served at /report
│       └── styles.css       # Shared styles
├── mcp_server/
│   └── server.py            # Standalone MCP server (FastMCP): 3 tools for Claude Desktop
├── scripts/
│   ├── setup_assistant.py   # One-time: enable pgvector, create tables, embed all activities
│   └── weekly_job.py        # Standalone script: sync + compute stats + send WhatsApp
├── tests/
│   ├── conftest.py          # Session-scoped Postgres setup/teardown, AsyncClient fixture
│   ├── test_assistant.py    # /assistant/ask, /reindex, build_activity_summary tests
│   ├── test_oauth_state.py  # CSRF state token generation, expiry, one-time-use
│   ├── test_token_service.py # Token encryption / OAuthToken tests
│   ├── test_week1.py        # Config, security, health endpoint tests
│   ├── test_week2.py        # Strava sync pipeline and normalizer tests
│   └── test_step4_6.py      # Analytics aggregation and PR engine tests
├── .github/
│   └── workflows/
│       └── weekly.yml       # GitHub Actions cron: every Saturday 12:00 UTC
├── docker-compose.yml       # App + Postgres 16 + Redis 7 services
├── Dockerfile               # python:3.12-slim, installs requirements, runs uvicorn
├── requirements.txt         # Pinned Python dependencies
├── pytest.ini               # asyncio_mode=auto, asyncio_default_fixture_loop_scope=session
└── .env.example             # Template for all environment variables
```

## Getting Started

### Prerequisites

- Docker and Docker Compose (recommended), **or** Python 3.12 with a local Postgres 16 instance
- A Strava API application — create one at [strava.com/settings/api](https://www.strava.com/settings/api)
- Garmin Connect credentials (optional — enrichment is skipped if absent)
- A Gemini API key (optional — required for `/assistant/ask`, embeddings, and MCP server; free at [aistudio.google.com](https://aistudio.google.com))

### 1. Clone and configure

```bash
git clone <repo-url>
cd fitness-sync
cp .env.example .env
```

Edit `.env` with your values (see [Configuration Reference](#configuration-reference) for the full list). Minimum required for the sync pipeline:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fitness_sync
TOKEN_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REDIRECT_URI=http://localhost:8000/auth/callback
```

To enable the AI assistant:

```bash
GEMINI_API_KEY=AIza...        # free key from aistudio.google.com
ASSISTANT_MODEL=gemini-2.5-flash   # default
EMBEDDING_MODEL=gemini-embedding-2  # default
```

### 2. Run with Docker Compose

```bash
docker compose up
```

This starts three services:

- `app` — FastAPI server on `http://localhost:8000` with hot-reload
- `db` — Postgres 16 on port 5432 (opt-in: uncomment `DATABASE_URL` override in `docker-compose.yml`)
- `redis` — Redis 7 on port 6379 (optional; cache is a no-op if `REDIS_URL` is empty)

### 3. Run without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

With `DEBUG=true` in `.env`, `create_tables()` runs on startup and creates all tables automatically (including enabling the pgvector extension). For production, use Alembic migrations.

### 4. Connect Strava

```
http://localhost:8000/auth/strava/login
```

This redirects to Strava's OAuth consent screen. After you approve, Strava redirects back to `/auth/callback`, which stores encrypted tokens and forwards you to `/report`.

### 5. Trigger a manual sync

```bash
# Get your user_id first
curl http://localhost:8000/auth/me

# Sync all activities (replace <uuid> with the returned id)
curl "http://localhost:8000/sync/strava?user_id=<uuid>"
```

### 6. Set up the AI assistant (one-time)

After your first successful sync, run the setup script to embed all existing activities into pgvector:

```bash
.venv/bin/python -m scripts.setup_assistant
```

This will:
1. Verify `GEMINI_API_KEY` is configured
2. Enable the pgvector extension and create the `activity_embeddings` table
3. Embed every existing activity (one Gemini API call per activity)
4. Print a test `curl` command to verify the assistant is working

Future syncs embed new activities automatically — you only need to run this script once.

### 7. Use the browser chat UI

Open `http://localhost:8000/chat` to ask questions about your training in your browser. The same LangGraph agent that powers `POST /assistant/ask` runs behind the UI.

## API Reference

Interactive docs are available at `http://localhost:8000/docs`.

### Auth — `/auth`

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/strava/login` | Redirect to Strava OAuth consent screen (generates CSRF state token) |
| `GET` | `/auth/callback` | Strava OAuth callback; exchanges code, upserts user + `OAuthToken` |
| `GET` | `/auth/me` | Returns the first user (`id`, `username`, `profile_pic`) |
| `GET` | `/auth/garmin/verify` | Attempts Garmin login; returns `{"status": "connected"}` or 400 |

**`GET /auth/callback`** query parameters:

| Parameter | Type | Notes |
|---|---|---|
| `code` | string | Authorization code from Strava |
| `state` | string | CSRF token generated by `/auth/strava/login` |
| `scope` | string | Scopes granted (e.g. `read,activity:read_all`) |

On success, redirects to `/report?connected=strava`.

### Sync — `/sync`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/sync/strava` | `user_id` (UUID) | Full sync: fetch all Strava activities, enrich with Garmin, upsert, embed, recompute PRs |
| `GET` | `/sync/platform-status` | — | Returns connection status and expiry for all stored OAuth tokens |

```bash
curl "http://localhost:8000/sync/strava?user_id=550e8400-e29b-41d4-a716-446655440000"
# {"synced": 42}
```

### Activities — `/activities`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/activities` | `user_id` (UUID), `limit` (int, default 20) | List recent activities ordered by date descending |

### Analytics — `/analytics`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/analytics/weekly-report` | `user_id` (UUID), `week_offset` (int, default 0) | Full weekly stats with sport breakdowns, recovery, PRs, and comparison vs previous week |

`week_offset=0` is the current week (Mon–Sun); `-1` is last week.

### Assistant — `/assistant`

| Method | Path | Body / Query params | Description |
|---|---|---|---|
| `POST` | `/assistant/ask` | JSON body | Ask a natural-language question; returns a grounded answer and cited activity IDs |
| `POST` | `/assistant/reindex` | `user_id` (UUID, optional), `full` (bool, default false) | Embed activities missing a vector; `full=true` re-embeds everything |

**`POST /assistant/ask` request:**

```bash
curl -s -X POST http://localhost:8000/assistant/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How has my running pace trended this month?"}' \
  | python3 -m json.tool
```

**Request body:**

| Field | Type | Notes |
|---|---|---|
| `question` | string | Natural-language training question |
| `user_id` | UUID (optional) | Defaults to the first user in the database |

**Response (`AskResponse`):**

```json
{
  "answer": "Your average running pace has improved from 5'45\"/km in early May to 5'20\"/km this week...",
  "cited_activities": [
    "550e8400-e29b-41d4-a716-446655440001",
    "550e8400-e29b-41d4-a716-446655440002"
  ]
}
```

The `cited_activities` list contains UUIDs of activities that were returned by the `semantic_search` tool during the agent's reasoning. Activities cited only via `get_week_stats` do not appear here.

Returns `503` if `GEMINI_API_KEY` is not set.

**`POST /assistant/reindex` example:**

```bash
# Embed any activities missing a vector
curl -s -X POST "http://localhost:8000/assistant/reindex"
# {"embedded": 5}

# Re-embed everything (e.g. after changing EMBEDDING_MODEL)
curl -s -X POST "http://localhost:8000/assistant/reindex?full=true"
```

### Reports and utility endpoints

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/chat` | — | Browser chat UI for the training assistant |
| `GET` | `/report` | — | Browser-based weekly report SPA |
| `GET` | `/report/pdf` | `user_id` (UUID), `week_offset` (int, default 0) | Downloads the weekly report as a PDF |
| `POST` | `/report/whatsapp` | `user_id` (UUID), `week_offset` (int, default 0) | Sends the weekly WhatsApp report via Twilio |
| `GET` | `/health` | — | Database connectivity check; `{"status":"ok"}` or 503 |
| `GET` | `/` | — | `{"message": "Fitness Sync API — visit /docs"}` |

## MCP Server

The MCP server (`mcp_server/server.py`) is a standalone process that exposes your fitness data as native tools to Claude Desktop and any other MCP-compatible client. It reuses the same service layer as the FastAPI app — no duplicated logic.

### Available tools

| Tool | Description |
|---|---|
| `get_weekly_stats(week_offset)` | Aggregate training totals, sleep, and recovery for any week |
| `search_activities(query, k, sport)` | Semantic search over embedded activities |
| `ask_assistant(question)` | Full LangGraph agent — decides which tools to call and returns a grounded answer |

### Starting the MCP server

```bash
.venv/bin/python -m mcp_server.server
```

### Claude Desktop setup

Add the following to `~/.claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), replacing the paths with the absolute path to your fitness-sync directory:

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

Restart Claude Desktop — it will discover the three tools automatically. `GEMINI_API_KEY` must be set in `.env` for `search_activities` and `ask_assistant` to function.

## Background Jobs

### In-process scheduler (APScheduler)

`app/tasks/scheduler.py` starts an `AsyncIOScheduler` during app startup and registers one job:

| Job | Schedule | What it does |
|---|---|---|
| `auto_sync_job` | Every 30 minutes | Calls `SyncService.run()`, commits, embeds new activities, logs the result |

The scheduler shuts down cleanly in the FastAPI lifespan's shutdown phase.

### GitHub Actions — weekly WhatsApp report

`.github/workflows/weekly.yml` runs `python -m scripts.weekly_job` every Saturday at 12:00 UTC. The workflow:

1. Checks out the repo and installs `requirements.txt`.
2. Syncs Strava (best-effort; failure is logged but does not abort).
3. Computes this week's and last week's stats via `_compute_week_stats`.
4. Formats and sends the WhatsApp message via Twilio.

All secrets (`DATABASE_URL`, `STRAVA_*`, `TOKEN_ENCRYPTION_KEY`, `GARMIN_*`, `TWILIO_*`) must be added to the repository's Actions secrets. The workflow can also be triggered manually from the Actions tab via `workflow_dispatch`.

## Testing

Tests require a reachable Postgres instance (the URL from `DATABASE_URL` in `.env`). They do not require a running server — `httpx.AsyncClient` with `ASGITransport` calls the ASGI app directly.

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=app

# Run a single test file
pytest tests/test_assistant.py

# Run tests matching a name pattern
pytest tests/ -k "test_retry"
```

### How the test database works

`tests/conftest.py` registers a session-scoped `setup_test_db` fixture (autouse) that calls `Base.metadata.create_all` before any test and `Base.metadata.drop_all` after the session ends. If the database is unreachable, both calls are silently swallowed so that unit tests using `AsyncMock` can still run without a live DB.

**Important:** both `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = session` in `pytest.ini` must remain set. Changing either breaks the session-scoped database fixture.

### Test files

| File | What it covers |
|---|---|
| `test_assistant.py` | `build_activity_summary`, `/assistant/ask`, `/assistant/reindex`; mocks `run_agent` — no real Gemini calls |
| `test_week1.py` | Settings loading, Fernet encrypt/decrypt, health endpoint |
| `test_week2.py` | Strava sync pipeline, normalizer |
| `test_oauth_state.py` | CSRF state token generation, expiry, one-time-use |
| `test_token_service.py` | `OAuthToken` model and encryption round-trips |
| `test_step4_6.py` | Analytics aggregation, PR engine |

### Note on assistant tests

`tests/test_assistant.py` mocks `run_agent` at the endpoint level — CI never calls the real Gemini API. `build_activity_summary` is tested with `SimpleNamespace` objects rather than real SQLAlchemy model instances (SQLAlchemy instrumentation breaks `Activity.__new__`).

## Deployment

The `Dockerfile` uses `python:3.12-slim`, installs `requirements.txt`, and starts uvicorn on `${PORT:-8000}`. This is compatible with Koyeb, Railway, Render, and similar PaaS platforms that inject a `$PORT` environment variable.

```bash
# Build and run locally
docker build -t fitness-sync .
docker run --env-file .env -p 8000:8000 fitness-sync
```

For production:

- Set `DEBUG=false` to skip the `create_tables()` shortcut.
- Run database schema migrations with Alembic (no Alembic migrations are committed yet; `create_tables()` calls `Base.metadata.create_all` directly, which is safe for a fresh database).
- Point `DATABASE_URL` at a managed Postgres instance with the pgvector extension enabled (e.g. Neon). The `docker-compose.yml` reads `DATABASE_URL` from `.env` by default; the local `db` service is opt-in.
- Add all required secrets to your platform's secret store or GitHub Actions secrets.

## Configuration Reference

Complete list of environment variables read by `app/core/config.py`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | asyncpg connection string |
| `TOKEN_ENCRYPTION_KEY` | Yes | — | Fernet key for token encryption |
| `STRAVA_CLIENT_ID` | Yes | — | Strava API application client ID |
| `STRAVA_CLIENT_SECRET` | Yes | — | Strava API application client secret |
| `STRAVA_REDIRECT_URI` | No | `http://localhost:8000/auth/callback` | Must match Strava app settings |
| `GARMIN_EMAIL` | No | `""` | Garmin Connect account email |
| `GARMIN_PASSWORD` | No | `""` | Garmin Connect account password |
| `GARMINTOKENS` | No | `.garminconnect` | Directory for cached Garmin OAuth tokens |
| `GEMINI_API_KEY` | No | `""` | Required for `/assistant/ask`, embeddings, and MCP server |
| `ASSISTANT_MODEL` | No | `gemini-2.5-flash` | Gemini model for the LangGraph agent |
| `EMBEDDING_MODEL` | No | `gemini-embedding-2` | Gemini model for activity embeddings (768-dim) |
| `REDIS_URL` | No | `""` | Redis connection string; cache is a no-op if empty |
| `TWILIO_ACCOUNT_SID` | No | `""` | Twilio account SID for WhatsApp |
| `TWILIO_AUTH_TOKEN` | No | `""` | Twilio auth token |
| `TWILIO_WHATSAPP_FROM` | No | `""` | Sender number (`whatsapp:+1...`) |
| `TWILIO_WHATSAPP_TO` | No | `""` | Recipient number (`whatsapp:+1...`) |
| `DEBUG` | No | `false` | Enables `create_tables()` on startup and SQL query logging |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

## Data Model

Five tables are created by `Base.metadata.create_all` (with `DEBUG=true`) or the `setup_assistant.py` script:

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id` (UUID PK), `strava_id` (unique), `username`, `profile_pic`, `max_hr` | One row per Strava athlete |
| `oauth_tokens` | `id`, `user_id` (FK), `provider`, `access_token`, `refresh_token`, `expires_at` | Tokens are Fernet-encrypted; unique on `(user_id, provider)` |
| `activities` | `id`, `user_id` (FK), `strava_id`, sport fields, Garmin fields, `raw_payload` (JSONB) | Unique on `(user_id, strava_id)`; raw Strava JSON preserved for re-normalisation |
| `activity_embeddings` | `id`, `activity_id` (FK, unique), `user_id`, `summary` (Text), `embedding` (Vector(768)), `model` | HNSW cosine index (`ix_ae_hnsw`); cascades on activity deletion |
| `personal_records` | `id`, `user_id` (FK), `metric`, `value`, `achieved_at` | Unique on `(user_id, metric)`; tracks `longest_run`, `highest_elevation`, `longest_duration`, `best_avg_pace` |