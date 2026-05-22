import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse, parse_qs

import pytest
from app.core import security


# ── unit tests for helpers ────────────────────────────────────────────────────

class TestStateHelpers:
    def test_generate_returns_nonempty_string(self):
        s = security.generate_oauth_state()
        assert isinstance(s, str) and len(s) > 0
        security._oauth_states.pop(s, None)

    def test_verify_accepts_valid_state(self):
        s = security.generate_oauth_state()
        assert security.verify_oauth_state(s) is True

    def test_verify_consumes_state_one_time(self):
        s = security.generate_oauth_state()
        security.verify_oauth_state(s)
        assert security.verify_oauth_state(s) is False

    def test_verify_unknown_state(self):
        assert security.verify_oauth_state("not-a-real-token") is False

    def test_verify_expired_state(self):
        s = security.generate_oauth_state()
        security._oauth_states[s] = time.time() - 1  # force past expiry
        assert security.verify_oauth_state(s) is False


# ── integration tests against the FastAPI app ─────────────────────────────────

@pytest.mark.asyncio
class TestOAuthStateRoutes:
    async def test_login_redirect_includes_state(self, client):
        r = await client.get("/auth/strava/login", follow_redirects=False)
        assert r.status_code in (307, 302)
        location = r.headers["location"]
        params = parse_qs(urlparse(location).query)
        assert "state" in params
        assert params["state"][0]  # non-empty
        assert params["scope"][0] == "read,activity:read_all"

    async def test_callback_rejects_fake_state(self, client):
        r = await client.get("/auth/callback?code=x&state=fake")
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid or expired OAuth state"

    async def test_callback_rejects_expired_state(self, client):
        # Generate via login, then force-expire before using it
        login = await client.get("/auth/strava/login", follow_redirects=False)
        location = login.headers["location"]
        state = parse_qs(urlparse(location).query)["state"][0]
        security._oauth_states[state] = time.time() - 1

        r = await client.get(f"/auth/callback?code=x&state={state}")
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid or expired OAuth state"

    async def test_normal_flow_state_is_issued_and_valid(self, client):
        # The login redirect stores the state in _oauth_states
        login = await client.get("/auth/strava/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        assert state in security._oauth_states       # was stored
        assert security.verify_oauth_state(state)    # passes verification
        assert not security.verify_oauth_state(state)  # consumed (one-time use)
