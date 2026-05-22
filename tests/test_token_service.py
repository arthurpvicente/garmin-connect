import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.token_service import get_valid_strava_token


def _make_db_with_token(token):
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = token
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _make_token(expired: bool) -> MagicMock:
    token = MagicMock()
    token.provider = "strava"
    token.access_token = "encrypted_access"
    token.refresh_token = "encrypted_refresh"
    if expired:
        token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        token.expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    token.is_expired.return_value = expired
    return token


class TestGetValidStravaToken:
    async def test_valid_token_returned_without_refresh(self):
        token = _make_token(expired=False)
        db = _make_db_with_token(token)

        with patch("app.services.token_service.decrypt_token", return_value="plain_access"), \
             patch("app.services.token_service.httpx.AsyncClient") as mock_httpx:
            result = await get_valid_strava_token("some-user-id", db)

        assert result == "plain_access"
        mock_httpx.assert_not_called()  # no refresh HTTP call

    async def test_expired_token_triggers_refresh(self):
        token = _make_token(expired=True)
        db = _make_db_with_token(token)

        fake_refresh_response = MagicMock()
        fake_refresh_response.status_code = 200
        fake_refresh_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_at": int((datetime.now(timezone.utc) + timedelta(hours=6)).timestamp()),
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=fake_refresh_response)

        with patch("app.services.token_service.decrypt_token", return_value="plain_refresh"), \
             patch("app.services.token_service.encrypt_token", return_value="enc"), \
             patch("app.services.token_service.httpx.AsyncClient", return_value=mock_client):
            await get_valid_strava_token("some-user-id", db)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "refresh_token" in call_kwargs.kwargs.get("data", {}) or \
               "refresh_token" in (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})

    async def test_missing_token_raises_value_error(self):
        db = _make_db_with_token(None)
        with pytest.raises(ValueError, match="No Strava token found"):
            await get_valid_strava_token("some-user-id", db)
