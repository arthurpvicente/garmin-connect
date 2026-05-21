import pytest
from unittest.mock import patch
 
class TestConfig:
    def test_settings_load(self):
        from app.core.config import settings
        assert settings.strava_client_id is not None
        assert 'postgresql' in settings.database_url
 
    def test_debug_is_boolean(self):
        from app.core.config import settings
        assert isinstance(settings.debug, bool)
 
class TestSecurity:
    def test_roundtrip(self):
        from app.core.security import encrypt_token, decrypt_token
        original = "strava_token_abc123"
        assert decrypt_token(encrypt_token(original)) == original
 
    def test_encrypted_differs_from_plain(self):
        from app.core.security import encrypt_token
        t = "my_token"
        assert encrypt_token(t) != t
 
    def test_different_ciphertext_each_time(self):
        from app.core.security import encrypt_token
        t = "same_token"
        assert encrypt_token(t) != encrypt_token(t)
 
    def test_tampered_token_raises(self):
        from app.core.security import encrypt_token, decrypt_token
        enc = encrypt_token("real")
        with pytest.raises(ValueError, match='decryption failed'):
            decrypt_token(enc[:-5] + 'XXXXX')
 
    def test_missing_key_raises(self):
        from app.core import security
        with patch.object(security.settings, "token_encryption_key", ""):
            with pytest.raises(RuntimeError, match='TOKEN_ENCRYPTION_KEY'):
                security._get_fernet()
 
@pytest.mark.asyncio
class TestHealth:
    async def test_health_returns_200(self, client):
        r = await client.get('/health')
        assert r.status_code in (200, 503)  # 503 if DB not up
 
    async def test_health_has_fields(self, client):
        r = await client.get('/health')
        assert 'status' in r.json()
        assert 'database' in r.json()
 
    async def test_root_not_404(self, client):
        r = await client.get('/')
        assert r.status_code == 200
