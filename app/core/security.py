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
