# Fitness Sync

A personal fitness dashboard that pulls your Strava activities, enriches them with Garmin recovery metrics (sleep score, body battery, HRV), persists everything in Postgres, and delivers a weekly summary via WhatsApp.

## Overview

Fitness Sync solves the problem of fragmented fitness data: your running history lives in Strava while your recovery data lives in Garmin Connect. This service bridges them into a single Postgres database and surfaces the combined data through a REST API and a browser-based report.

**What it does:**

- Authenticates with Strava via OAuth 2.0 and continuously syncs your full activity history (runs, rides, swims, strength sessions, etc.).
- Enriches each activity with the Garmin metrics recorded on the same day: body battery at activity start time, HRV status, and previous night's sleep score.
- Tracks personal records across four metrics (longest run, highest elevation, longest duration, best average pace) and updates them on every sync.
- Exposes a `/analytics/weekly-report` endpoint that aggregates stats by sport category and compares the current week against the previous one.
- Sends a formatted WhatsApp message every Saturday via Twilio, and can export the same report as a PDF.
- Runs an in-process APScheduler job every 30 minutes to keep the database current without manual intervention.

## Tech Stack

| Concern | Library / Tool | Version |
|---|---|---|
| Language / runtime | Python | 3.12 |
| Web framework | FastAPI | 0.115.0 |
| ASGI server | Uvicorn (with standard extras) | 0.30.6 |
| ORM | SQLAlchemy async | 2.0.36 |
| Database driver | asyncpg | 0.30.0 |
| Database | PostgreSQL | 16 (Docker image) |
| Settings | pydantic-settings | 2.5.2 |
| HTTP client | httpx | 0.27.2 |
| Garmin integration | garminconnect | 0.2.38 |
| Token encryption | cryptography (Fernet) | 43.0.3 |
| JWT / signing | python-jose | 3.3.0 |
| In-process scheduler | APScheduler (AsyncIOScheduler) | 3.10.4 |
| Cache | Redis (redis[hiredis]) | 5.1.1 |
| PDF generation | fpdf2 | 2.7.9 |
| Testing | pytest + pytest-asyncio | 8.3.3 / 0.24.0 |
| Containerisation | Docker Compose | — |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI app  (app/main.py)                             │
│                                                         │
│  /auth   router  ──────────────────────────────────┐    │
│  /sync   router  ──────────────────────────────┐   │    │
│  /analytics router ──────────────────────┐     │   │    │
│  /activities router ─────────────────┐   │     │   │    │
│  /report, /report/pdf, /report/whatsapp   │   │   │    │
│  /health, /                           │   │   │   │    │
│                                       │   │   │   │    │
│  APScheduler (every 30 min) ──────────┘   │   │   │    │
└───────────────────────────────────────────┼───┼───┼────┘
                                            │   │   │
                              ┌─────────────┘   │   └─────────────┐
                              │                 │                 │
                        Postgres DB       Redis cache        Strava API
                        (activities,      (analytics         Garmin Connect
                         oauth_tokens,     results)          Twilio WhatsApp
                         users, PRs)
```

### Request flow — Strava sync

```
GET /sync/strava?user_id=<uuid>
  → StravaClient._load_token()         # fetch OAuthToken from DB (app/services/strava_client.py)
  → StravaClient._get_valid_access_token()  # refresh via Strava if is_expired() (5-min buffer)
  → StravaClient.get_activities()      # paginate /athlete/activities 100/page, @with_retry 429s
  → normalizer.normalize_strava_activity()  # map raw JSON → Activity kwargs (app/services/normalizer.py)
  → garmin_client.fetch_daily_metrics()     # body_battery, hrv, sleep, stress, steps per day
  → normalizer.enrich_with_garmin()    # attach Garmin fields to activity dict
  → activity_service.upsert_activities()   # ON CONFLICT (user_id, strava_id) DO UPDATE
  → pr_engine.check_and_update_prs()   # upsert personal records across all 4 metrics
  → cache_invalidate_user()            # bust Redis keys matching analytics:<user_id>:*
```

The in-process scheduler (`app/tasks/scheduler.py`) calls `SyncService.run()` directly every 30 minutes, bypassing the HTTP layer.

### OAuth state / CSRF defense

`app/core/security.py` maintains an in-memory dict (`_oauth_states`) mapping 32-byte URL-safe tokens to expiry timestamps (5-minute TTL). `generate_oauth_state()` mints a token; `verify_oauth_state()` consumes it with `pop()` (one-time use). The `/auth/strava/login` endpoint generates a state token and redirects the user to Strava; `/auth/callback` verifies it before exchanging the authorization code.

**Production note:** the in-memory dict does not survive restarts and does not work across multiple workers. Replace with Redis for multi-instance deployments.

### Token storage and encryption

`OAuthToken` (`app/models/token.py`) stores access and refresh tokens encrypted with Fernet symmetric encryption (`app/core/security.py: encrypt_token / decrypt_token`). The encryption key is loaded from `TOKEN_ENCRYPTION_KEY` at startup; if the key is missing, the app raises a `RuntimeError` immediately. The unique constraint is `(user_id, provider)`, so reconnecting replaces the existing token row.

### Retry strategy

`app/core/retry.py` provides the `@with_retry(max_attempts, base_delay)` decorator that catches `httpx.HTTPStatusError` with status 429 and retries with exponential backoff (default: 5 attempts, 1 s base). Applied to `StravaClient.get_activities()`.

Garmin login and daily metric fetches each have their own inline retry loops that catch `GarminConnectTooManyRequestsError` with the same exponential backoff pattern. The Garmin client is cached at module level so login happens at most once per process.

### Garmin enrichment

Garmin enrichment is best-effort. If `GARMIN_EMAIL` / `GARMIN_PASSWORD` are not set, or if the Garmin API is unreachable, the enrichment step is skipped for that day and the activity is still saved with null Garmin fields. The sync never fails because Garmin is unavailable.

## Project Structure

```
fitness-sync/
├── app/
│   ├── main.py              # FastAPI app, lifespan, router mounts, top-level endpoints
│   ├── api/
│   │   ├── auth.py          # /auth router: Strava OAuth, Garmin verify
│   │   ├── sync.py          # /sync router: trigger sync, platform status
│   │   ├── analytics.py     # /analytics router: weekly-report endpoint
│   │   └── activities.py    # /activities router: list recent activities
│   ├── core/
│   │   ├── config.py        # pydantic-settings Settings class (single shared instance)
│   │   ├── database.py      # async SQLAlchemy engine, session factory, Base, create_tables
│   │   ├── security.py      # Fernet encrypt/decrypt, OAuth state CSRF tokens
│   │   ├── retry.py         # @with_retry decorator (exponential backoff on 429)
│   │   └── cache.py         # Redis async client, cache_get/set/invalidate (no-op if no Redis)
│   ├── models/
│   │   ├── user.py          # User table (strava_id, username, profile_pic, max_hr)
│   │   ├── token.py         # OAuthToken table (encrypted access/refresh, is_expired())
│   │   ├── activity.py      # Activity table (Strava fields + Garmin enrichment + raw JSONB)
│   │   └── personal_record.py  # PersonalRecord table (one row per user+metric)
│   ├── services/
│   │   ├── strava_client.py    # StravaClient: load/refresh token, paginate activities
│   │   ├── garmin_client.py    # Module-level cached Garmin client, fetch_daily_metrics
│   │   ├── normalizer.py       # normalize_strava_activity, enrich_with_garmin
│   │   ├── activity_service.py # upsert_activities (Postgres INSERT … ON CONFLICT DO UPDATE)
│   │   ├── sync_service.py     # SyncService.run() orchestrates the full sync pipeline
│   │   ├── pr_engine.py        # check_and_update_prs across 4 metrics
│   │   ├── whatsapp.py         # Twilio send_message, format_weekly_report text
│   │   └── pdf_report.py       # generate_weekly_pdf (fpdf2)
│   ├── tasks/
│   │   └── scheduler.py     # APScheduler setup, auto_sync_job (every 30 min)
│   └── static/
│       └── report.html      # Browser-based weekly report UI (served at /report)
├── scripts/
│   └── weekly_job.py        # Standalone script: sync + compute stats + send WhatsApp
├── tests/
│   ├── conftest.py          # Session-scoped Postgres setup/teardown, AsyncClient fixture
│   ├── test_week1.py        # Config, security, health endpoint tests
│   ├── test_week2.py        # Sync pipeline and normalizer tests
│   ├── test_oauth_state.py  # CSRF state token unit tests
│   ├── test_token_service.py # Token encryption / OAuthToken tests
│   └── test_step4_6.py      # Analytics and PR engine tests
├── .github/
│   └── workflows/
│       └── weekly.yml       # GitHub Actions cron: every Saturday 12:00 UTC
├── docker-compose.yml       # App + Postgres 16 + Redis 7 services
├── Dockerfile               # python:3.12-slim, installs requirements, runs uvicorn
├── requirements.txt         # Pinned Python dependencies
├── pytest.ini               # asyncio_mode=auto, asyncio_default_fixture_loop_scope=session
└── .env.example             # Template for required environment variables
```

## Getting Started

### Prerequisites

- Docker and Docker Compose (recommended), **or** Python 3.12 with a local Postgres 16 instance
- A Strava API application (create one at [strava.com/settings/api](https://www.strava.com/settings/api))
- Garmin Connect credentials (optional — enrichment is skipped if absent)

### 1. Clone and configure

```bash
git clone <repo-url>
cd fitness-sync
cp .env.example .env
```

Edit `.env` and fill in the required values:

| Variable | Example / Notes |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/fitness_sync` |
| `TOKEN_ENCRYPTION_KEY` | Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `STRAVA_CLIENT_ID` | From your Strava API application settings |
| `STRAVA_CLIENT_SECRET` | From your Strava API application settings |
| `STRAVA_REDIRECT_URI` | `http://localhost:8000/auth/callback` |
| `GARMIN_EMAIL` | Optional — enrichment is skipped if blank |
| `GARMIN_PASSWORD` | Optional — enrichment is skipped if blank |
| `GARMINTOKENS` | Path to cache Garmin OAuth tokens; default `.garminconnect` |
| `REDIS_URL` | `redis://localhost:6379/0` — optional, cache is no-op if empty |
| `DEBUG` | `true` auto-creates tables on startup (dev only) |
| `LOG_LEVEL` | `INFO` |
| `TWILIO_ACCOUNT_SID` | Optional — required only for WhatsApp reports |
| `TWILIO_AUTH_TOKEN` | Optional |
| `TWILIO_WHATSAPP_FROM` | e.g. `whatsapp:+14155238886` (Twilio sandbox number) |
| `TWILIO_WHATSAPP_TO` | e.g. `whatsapp:+15551234567` (your number with country code) |

### 2. Run with Docker Compose

```bash
docker compose up
```

This starts three services:

- `app` — the FastAPI server on `http://localhost:8000` (hot-reload enabled via volume mount)
- `db` — Postgres 16 on port 5432 (comment out `DATABASE_URL` override in `docker-compose.yml` to use it)
- `redis` — Redis 7 on port 6379

By default `docker-compose.yml` reads `DATABASE_URL` from your `.env`, so the app connects wherever that URL points (e.g. Neon). Uncomment the `DATABASE_URL` override in `docker-compose.yml` to use the local `db` service instead.

### 3. Run without Docker

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Start Postgres separately (or point DATABASE_URL at an existing instance)
uvicorn app.main:app --reload
```

With `DEBUG=true` in `.env`, `create_tables()` runs on startup and creates all tables automatically. For production, use Alembic migrations.

### 4. Connect Strava

Open your browser and visit:

```
http://localhost:8000/auth/strava/login
```

This redirects you to Strava's OAuth consent screen. After you approve, Strava redirects back to `/auth/callback`, which stores your encrypted tokens and forwards you to `/report`.

### 5. Trigger a manual sync

```bash
# Get your user_id first
curl http://localhost:8000/auth/me

# Then sync (replace <uuid> with the returned id)
curl "http://localhost:8000/sync/strava?user_id=<uuid>"
```

## API Reference

Interactive docs are available at `http://localhost:8000/docs` when the server is running.

### Auth — `/auth`

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/strava/login` | Redirect to Strava OAuth consent screen (sets CSRF state cookie) |
| `GET` | `/auth/callback` | Strava OAuth callback; exchanges code for tokens, upserts user + `OAuthToken` |
| `GET` | `/auth/me` | Returns the first user in the DB (`id`, `username`, `profile_pic`) |
| `GET` | `/auth/garmin/verify` | Attempts Garmin login; returns `{"status": "connected"}` or 400 |

**`GET /auth/callback`** query parameters:

| Parameter | Type | Notes |
|---|---|---|
| `code` | string | Authorization code from Strava |
| `state` | string | CSRF token generated by `/auth/strava/login` |
| `scope` | string | Scopes granted by the user (e.g. `read,activity:read_all`) |

On success, redirects to `/report?connected=strava`.

### Sync — `/sync`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/sync/strava` | `user_id` (UUID) | Full sync: fetch all Strava activities, enrich with Garmin, upsert, recompute PRs |
| `GET` | `/sync/platform-status` | — | Returns connection status and expiry for all stored OAuth tokens |

**`GET /sync/strava` example:**

```bash
curl "http://localhost:8000/sync/strava?user_id=550e8400-e29b-41d4-a716-446655440000"
# {"synced": 42}
```

**`GET /sync/platform-status` example response:**

```json
{
  "strava": {
    "connected": true,
    "expires_at": "2026-05-25T14:00:00+00:00",
    "is_expired": false,
    "scopes": ["read", "activity:read_all"],
    "updated_at": "2026-05-25T08:00:00+00:00"
  }
}
```

### Activities — `/activities`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/activities` | `user_id` (UUID), `limit` (int, default 20) | List recent activities ordered by date descending |

**Example response item:**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "strava_id": 12345678,
  "type": "run",
  "strava_type": "Run",
  "name": "Morning Run",
  "start_date": "2026-05-24T07:30:00+00:00",
  "distance_m": 10250.0,
  "duration_s": 3120,
  "avg_heart_rate": 152.0,
  "max_heart_rate": 174.0,
  "elevation_m": 85.0,
  "avg_pace_s_km": 304.4,
  "avg_speed_ms": 3.29,
  "calories": 620,
  "suffer_score": 68,
  "kudos_count": 3,
  "platform": "strava"
}
```

### Analytics — `/analytics`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/analytics/weekly-report` | `user_id` (UUID), `week_offset` (int, default 0) | Full weekly stats with sport breakdowns, recovery, day-by-day summary, PRs, and comparison vs previous week |

`week_offset=0` returns the current week (Mon–Sun); `-1` returns last week, and so on.

**Abbreviated example response:**

```json
{
  "week_label": "May 19 – 25, 2026",
  "week_start": "2026-05-19",
  "week_end": "2026-05-25",
  "highlight": "Longest run: 15.2 km in 1h 22m on Sat",
  "running": {
    "sessions": 3,
    "total_km": 32.4,
    "avg_pace_fmt": "4'58\"",
    "total_elevation_m": 210,
    "longest_km": 15.2,
    "total_duration_min": 161
  },
  "sleep": { "avg_score": 78, "nights_tracked": 5 },
  "recovery": { "avg_body_battery": 62, "hrv_breakdown": {"Balanced": 4, "Low": 1} },
  "prs": [{ "metric": "longest_run", "value": 15200.0, "achieved_at": "2026-05-24T07:30:00+00:00" }],
  "totals": { "active_days": 5, "total_activities": 7, "total_calories": 3100, "total_duration_min": 310 },
  "comparison": { "running_km_delta": 4.2, "running_km_pct": 15, "active_days_delta": 1 }
}
```

### Reports and utility endpoints — `/report`, `/health`

| Method | Path | Query params | Description |
|---|---|---|---|
| `GET` | `/report` | — | Serves the browser-based weekly report SPA (`app/static/report.html`) |
| `GET` | `/report/pdf` | `user_id` (UUID), `week_offset` (int, default 0) | Downloads the weekly report as a PDF file |
| `POST` | `/report/whatsapp` | `user_id` (UUID), `week_offset` (int, default 0) | Immediately sends the weekly WhatsApp report via Twilio |
| `GET` | `/health` | — | Database connectivity check; returns 200 `{"status":"ok"}` or 503 `{"status":"degraded"}` |
| `GET` | `/` | — | Returns `{"message": "Fitness Sync API — visit /docs"}` |

## Background Jobs

### In-process scheduler (APScheduler)

`app/tasks/scheduler.py` starts an `AsyncIOScheduler` during app startup and registers one job:

| Job | Schedule | What it does |
|---|---|---|
| `auto_sync_job` | Every 30 minutes | Calls `SyncService.run()`, commits, logs the result |

The scheduler is shut down cleanly in the FastAPI lifespan's shutdown phase.

### GitHub Actions — weekly WhatsApp report

`.github/workflows/weekly.yml` runs `python -m scripts.weekly_job` every Saturday at 12:00 UTC (09:00 BRT). The workflow:

1. Checks out the repo and installs dependencies from `requirements.txt`.
2. Runs `scripts/weekly_job.py`, which:
   - Syncs Strava (best-effort; failure is logged but does not abort)
   - Computes this week's and last week's stats via `_compute_week_stats`
   - Formats and sends the WhatsApp message via Twilio

All secrets (`DATABASE_URL`, `STRAVA_*`, `TOKEN_ENCRYPTION_KEY`, `GARMIN_*`, `TWILIO_*`) must be added to the repository's Actions secrets. The workflow can also be triggered manually from the Actions tab via `workflow_dispatch`.

## Testing

Tests require a reachable Postgres instance (the URL from `DATABASE_URL` in `.env`). They do **not** require a running server — `httpx.AsyncClient` with `ASGITransport` is used to call the ASGI app directly.

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=app

# Run a single test file
pytest tests/test_oauth_state.py

# Run tests matching a name pattern
pytest tests/ -k "test_retry"
```

### How the test database works

`tests/conftest.py` registers a session-scoped `setup_test_db` fixture (autouse) that calls `Base.metadata.create_all` before any test runs and `Base.metadata.drop_all` after the session ends. If the database is unreachable, both calls are silently swallowed so that unit tests that use `AsyncMock` can still run without a live DB.

**Important `pytest.ini` settings:** both `asyncio_mode = auto` and `asyncio_default_fixture_loop_scope = session` must remain set. Changing either breaks the session-scoped database fixture.

### Test files

| File | What it covers |
|---|---|
| `test_week1.py` | Settings loading, Fernet encrypt/decrypt, health endpoint |
| `test_week2.py` | Strava sync pipeline, normalizer |
| `test_oauth_state.py` | CSRF state token generation, expiry, one-time-use |
| `test_token_service.py` | `OAuthToken` model and encryption round-trips |
| `test_step4_6.py` | Analytics aggregation, PR engine |

## Deployment

The `Dockerfile` uses `python:3.12-slim`, installs `requirements.txt`, and starts uvicorn on `${PORT:-8000}`. This pattern is compatible with Koyeb, Railway, Render, and similar PaaS platforms that inject a `$PORT` environment variable.

```bash
# Build and run locally
docker build -t fitness-sync .
docker run --env-file .env -p 8000:8000 fitness-sync
```

For production:

- Set `DEBUG=false` to skip the `create_tables()` shortcut.
- Run database schema migrations with Alembic (no Alembic migrations are committed yet; `create_tables()` calls `Base.metadata.create_all` directly, which is safe for a fresh database).
- Point `DATABASE_URL` at a managed Postgres instance (e.g. Neon). The `docker-compose.yml` is pre-wired for this: `DATABASE_URL` comes from `.env` and the local `db` service is opt-in.
- Add all required secrets to your platform's secret store or GitHub Actions secrets (for the weekly job).

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
| `REDIS_URL` | No | `""` | Redis connection string; cache disabled if empty |
| `TWILIO_ACCOUNT_SID` | No | `""` | Twilio account SID for WhatsApp |
| `TWILIO_AUTH_TOKEN` | No | `""` | Twilio auth token |
| `TWILIO_WHATSAPP_FROM` | No | `""` | Sender number (`whatsapp:+1...`) |
| `TWILIO_WHATSAPP_TO` | No | `""` | Recipient number (`whatsapp:+1...`) |
| `DEBUG` | No | `false` | Enables `create_tables()` on startup and SQL query logging |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

## Data Model

Four tables are created by `Base.metadata.create_all`:

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id` (UUID PK), `strava_id` (unique), `username`, `profile_pic`, `max_hr` | One row per Strava athlete |
| `oauth_tokens` | `id` (UUID PK), `user_id` (FK), `provider`, `access_token`, `refresh_token`, `expires_at` | Tokens are Fernet-encrypted; unique on `(user_id, provider)` |
| `activities` | `id` (UUID PK), `user_id` (FK), `strava_id`, sport fields, Garmin fields, `raw_payload` (JSONB) | Unique on `(user_id, strava_id)`; raw Strava JSON preserved for re-normalisation |
| `personal_records` | `id` (UUID PK), `user_id` (FK), `metric`, `value`, `achieved_at` | Unique on `(user_id, metric)`; tracks `longest_run`, `highest_elevation`, `longest_duration`, `best_avg_pace` |
