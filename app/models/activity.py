import uuid
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, func, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
 
class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("user_id", "strava_id", name="uq_user_strava_activity"),
        Index("ix_activities_user_date", "user_id", "start_date"),
    )
 
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    strava_id: Mapped[int] = mapped_column(BigInteger, index=True)
    type: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[int] = mapped_column(Integer, default=0)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_speed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pace_s_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kudos_count: Mapped[int] = mapped_column(Integer, default=0)
    suffer_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trainer: Mapped[bool] = mapped_column(Boolean, default=False)
    commute: Mapped[bool] = mapped_column(Boolean, default=False)
    # Garmin enrichment (nullable — only if Garmin is configured)
    body_battery_at_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hrv_status_on_day: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sleep_score_prev_night: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Original Strava JSON — lets you re-normalize later without re-fetching
    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
