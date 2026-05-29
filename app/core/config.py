# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
 
class Settings(BaseSettings):
    # Strava OAuth
    strava_client_id: str
    strava_client_secret: str
    strava_redirect_uri: str = "http://localhost:8000/auth/callback"

    # Garmin (optional)
    garmin_email: str = ""
    garmin_password: str = ""
    garmintokens: str = ".garminconnect"
 
    # Database
    database_url: str
 
    # Redis (optional — cache only; app runs fine without it)
    redis_url: str = ""
 
    # Encryption key — required, no default
    token_encryption_key: str
 
    # RAG assistant (optional — /assistant/ask requires this)
    gemini_api_key: str = ""
    assistant_model: str = "gemini-2.5-flash"
    embedding_model: str = "gemini-embedding-2"

    # Twilio WhatsApp (optional)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_whatsapp_to: str = ""

    # App
    debug: bool = False
    log_level: str = "INFO"
 
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
 
# Single shared instance — import this everywhere
settings = Settings()
