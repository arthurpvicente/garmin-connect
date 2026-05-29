"""
Tests for the RAG training assistant.

build_activity_summary — pure unit tests, no mocking needed.
/assistant/ask + /assistant/reindex — endpoint tests that mock run_agent
  and embed_activities so CI never hits live APIs.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.embeddings import build_activity_summary


# ---------------------------------------------------------------------------
# Helper: SimpleNamespace acts like an Activity for attribute reads.
# We can't use Activity.__new__ because SQLAlchemy's instrumentation
# requires a properly initialised instance state.
# ---------------------------------------------------------------------------

def _make_activity(**kwargs) -> SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        strava_id=42,
        type="Run",
        name="Morning Run",
        start_date=datetime(2026, 5, 28, 7, 0, tzinfo=timezone.utc),
        distance_m=5000.0,
        duration_s=1800,
        elevation_m=50.0,
        avg_heart_rate=145.0,
        avg_pace_s_km=360.0,
        calories=400,
        suffer_score=45,
        body_battery_at_start=65,
        hrv_status_on_day="Balanced",
        sleep_score_prev_night=78,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Unit tests — build_activity_summary
# ---------------------------------------------------------------------------

class TestBuildActivitySummary:
    def test_date_and_sport_always_present(self):
        a = _make_activity()
        s = build_activity_summary(a)
        assert "2026" in s
        assert "Morning Run" in s
        assert "running" in s

    def test_distance_and_duration(self):
        a = _make_activity()
        s = build_activity_summary(a)
        assert "5.00 km" in s
        assert "30 min" in s

    def test_pace_and_heart_rate(self):
        a = _make_activity()
        s = build_activity_summary(a)
        # avg_pace_s_km=360 → 6'00"/km
        assert "6'00\"/km" in s
        assert "145 bpm" in s

    def test_calories_and_elevation(self):
        a = _make_activity()
        s = build_activity_summary(a)
        assert "400 kcal" in s
        assert "50 m elevation" in s

    def test_garmin_fields_included(self):
        a = _make_activity()
        s = build_activity_summary(a)
        assert "sleep score 78/100" in s
        assert "body battery 65%" in s
        assert "HRV status Balanced" in s

    def test_missing_optional_fields_dont_appear(self):
        a = _make_activity(
            distance_m=None,
            avg_pace_s_km=None,
            avg_heart_rate=None,
            calories=None,
            elevation_m=None,
            suffer_score=None,
            sleep_score_prev_night=None,
            body_battery_at_start=None,
            hrv_status_on_day=None,
        )
        s = build_activity_summary(a)
        assert "km" not in s
        assert "bpm" not in s
        assert "kcal" not in s
        assert "Garmin" not in s

    def test_strength_categorised_correctly(self):
        a = _make_activity(type="WeightTraining", name="Gym session", distance_m=None)
        s = build_activity_summary(a)
        assert "strength" in s

    def test_returns_string(self):
        a = _make_activity()
        assert isinstance(build_activity_summary(a), str)


# ---------------------------------------------------------------------------
# Endpoint tests — /assistant/ask
# ---------------------------------------------------------------------------

FAKE_USER_ID = str(uuid.uuid4())
FAKE_ACTIVITY_ID = str(uuid.uuid4())


@pytest.mark.asyncio
async def test_ask_returns_answer_when_configured():
    """Happy path: key set, agent runs and returns a grounded answer."""
    with (
        patch("app.api.assistant.settings") as mock_settings,
        patch(
            "app.api.assistant.run_agent",
            new=AsyncMock(
                return_value=("Your pace has improved by 15 seconds/km.", [FAKE_ACTIVITY_ID])
            ),
        ),
    ):
        mock_settings.gemini_api_key = "fake-gemini-key"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/assistant/ask",
                json={"question": "How is my pace trending?", "user_id": FAKE_USER_ID},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert "answer" in body
    assert "cited_activities" in body
    assert FAKE_ACTIVITY_ID in body["cited_activities"]
    assert "pace" in body["answer"]


@pytest.mark.asyncio
async def test_ask_returns_503_when_keys_missing():
    """If GEMINI_API_KEY is not set the endpoint returns 503 with a clear message."""
    with patch("app.api.assistant.settings") as mock_settings:
        mock_settings.gemini_api_key = ""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/assistant/ask",
                json={"question": "How is my pace?", "user_id": FAKE_USER_ID},
            )
    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ask_returns_answer_with_no_citations():
    """Agent can answer stats-only questions without any cited activities."""
    with (
        patch("app.api.assistant.settings") as mock_settings,
        patch(
            "app.api.assistant.run_agent",
            new=AsyncMock(return_value=("You ran 25 km this week.", [])),
        ),
    ):
        mock_settings.gemini_api_key = "fake-gemini-key"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/assistant/ask",
                json={"question": "How much did I run this week?", "user_id": FAKE_USER_ID},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["cited_activities"] == []
    assert "25 km" in body["answer"]
