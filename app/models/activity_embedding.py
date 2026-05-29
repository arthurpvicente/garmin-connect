import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from app.core.database import Base


class ActivityEmbedding(Base):
    __tablename__ = "activity_embeddings"
    __table_args__ = (
        # HNSW index for fast cosine-similarity search.
        # Without this, every query would scan all rows — fine for 100 activities,
        # very slow for 10 000+.
        Index(
            "ix_ae_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"), unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    summary: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(768))
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
