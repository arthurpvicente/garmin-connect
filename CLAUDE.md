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

`DEBUG=true` makes the app call `create_tables()` on startup (dev convenience). In production, use Alembic migrations instead.

## Architecture

FastAPI app with async SQLAlchemy + asyncpg (Postgres). Two routers mount at startup: `/auth` and `/sync`. An APScheduler job runs `auto_sync_job` every 30 minutes in-process.

### Request flow — Strava sync

```
GET /sync/strava?user_id=<uuid>
  → token_service.get_valid_strava_token()   # loads OAuthToken from DB, refreshes if is_expired()
  → strava_client.StravaClient.get_activities()  # paginates all pages (100/page), @with_retry for 429s
  → normalizer.normalize_strava_activity()   # maps raw Strava JSON → Activity kwargs
  → garmin_client.fetch_daily_metrics()      # optional enrichment per day, retries on rate limit
  → normalizer.enrich_with_garmin()
  → activity_service.upsert_activities()     # ON CONFLICT (user_id, strava_id) DO UPDATE
```

The scheduler's `auto_sync_job` (in `tasks/scheduler.py`) uses `StravaClient` + `SyncService` directly rather than going through the HTTP endpoint.

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
