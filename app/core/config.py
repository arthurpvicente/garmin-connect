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
 
    # Redis
    redis_url: str = "redis://localhost:6379/0"
 
    # Encryption key — required, no default
    token_encryption_key: str
 
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
