"""Tests for retry decorator, upsert service, and paginated Strava fetch."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx

from app.core.retry import with_retry
from app.services.activity_service import upsert_activities
from app.services.strava_client import StravaClient


# ── helpers ───────────────────────────────────────────────────────────────────

def _http_error(status_code: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status_code)
    return httpx.HTTPStatusError("error", request=httpx.Request("GET", "http://x"), response=response)


# ── retry decorator ───────────────────────────────────────────────────────────

class TestWithRetry:
    async def test_non_429_raises_immediately(self):
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            raise _http_error(500)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await flaky()

        assert exc_info.value.response.status_code == 500
        assert call_count == 1  # no retries on non-429

    async def test_429_retries_then_succeeds(self):
        calls = []

        @with_retry(max_attempts=5, base_delay=0.0)
        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(429)
            return "ok"

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await flaky()

        assert result == "ok"
        assert len(calls) == 3
        assert mock_sleep.call_count == 2  # slept before attempt 2 and 3

    async def test_429_exhausts_all_attempts(self):
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.0)
        async def always_429():
            nonlocal call_count
            call_count += 1
            raise _http_error(429)

        with patch("app.core.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await always_429()

        assert exc_info.value.response.status_code == 429
        assert call_count == 3
        assert mock_sleep.call_count == 2  # slept max_attempts - 1 times


# ── upsert_activities ─────────────────────────────────────────────────────────

class TestUpsertActivities:
    async def test_empty_list_returns_zero_without_db_call(self):
        db = AsyncMock()
        result = await upsert_activities([], db)
        assert result == 0
        db.execute.assert_not_called()

    async def test_inserts_rows_and_commits(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        db.execute = AsyncMock(return_value=mock_result)

        activities = [
            {"user_id": "uid", "strava_id": i, "name": f"Run {i}"}
            for i in range(3)
        ]
        count = await upsert_activities(activities, db)

        assert count == 3
        db.execute.assert_called_once()
        db.commit.assert_called_once()


# ── paginated get_activities ──────────────────────────────────────────────────

def _make_strava_client() -> StravaClient:
    client = StravaClient(db=AsyncMock())
    return client


def _mock_get_side_effect(pages: list[list[dict]]):
    """Returns a side_effect that yields successive pages then empty."""
    call_num = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_num
        page_data = pages[call_num] if call_num < len(pages) else []
        call_num += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = page_data
        resp.raise_for_status = MagicMock()
        return resp

    return _side_effect


class TestStravaClientPagination:
    async def test_single_page_fetches_twice(self):
        strava = _make_strava_client()
        page1 = [{"id": 1}, {"id": 2}]

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=_mock_get_side_effect([page1, []]))

        with patch.object(strava, "_get_valid_access_token", AsyncMock(return_value="tok")), \
             patch("app.services.strava_client.httpx.AsyncClient", return_value=mock_client):
            result = await strava.get_activities()

        assert result == page1
        assert mock_client.get.call_count == 2  # page 1 + empty page 2

    async def test_multiple_pages_fetched_all(self):
        strava = _make_strava_client()
        page1 = [{"id": i} for i in range(3)]
        page2 = [{"id": i} for i in range(3, 6)]
        page3 = [{"id": i} for i in range(6, 9)]

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(
            side_effect=_mock_get_side_effect([page1, page2, page3, []])
        )

        with patch.object(strava, "_get_valid_access_token", AsyncMock(return_value="tok")), \
             patch("app.services.strava_client.httpx.AsyncClient", return_value=mock_client):
            result = await strava.get_activities()

        assert len(result) == 9
        assert mock_client.get.call_count == 4  # 3 data pages + 1 empty
