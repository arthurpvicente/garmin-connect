from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings
 
def _get_fernet() -> Fernet:
    key = settings.token_encryption_key
    if not key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())
 
def encrypt_token(plain_text: str) -> str:
    """Encrypt a token for storage in the DB."""
    return _get_fernet().encrypt(plain_text.encode()).decode()
 
def decrypt_token(encrypted_text: str) -> str:
    """Decrypt a stored token back to plain text."""
    try:
        return _get_fernet().decrypt(encrypted_text.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Token decryption failed — ciphertext may be corrupted or key changed. "
            "User must re-authenticate."
        ) from e

import secrets
import time

# In-memory store: { state_token: expiry_unix_timestamp }
_oauth_states: dict[str, float] = {}
STATE_TTL_SECONDS = 300  # 5 minutes


def generate_oauth_state() -> str:
    """Generate a secure random token and store it with a TTL."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.time() + STATE_TTL_SECONDS
    return state


def verify_oauth_state(state: str) -> bool:
    """
    Verify the state token exists and has not expired.
    Uses pop() so the token is deleted after first use (one-time-use).
    """
    expiry = _oauth_states.pop(state, None)  # deletes on read
    if expiry is None:
        return False  # was never issued
    if time.time() > expiry:
        return False  # expired
    return True
