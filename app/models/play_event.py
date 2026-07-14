"""PlayEvent model — tracks individual song plays for engagement analytics."""

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayEvent(TimestampMixin, Base):
    """A single play event — recorded when a user listens for 30+ seconds."""

    __tablename__ = "play_events"

    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    track_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    artist: Mapped[str] = mapped_column(String(512), nullable=False)
    album: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    listened_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[bool] = mapped_column(default=False)
    hour_of_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[bool] = mapped_column(default=False)
    thumbs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="slaplayer")

    __table_args__ = (
        Index("ix_play_events_username_created", "username", "created_at"),
        Index("ix_play_events_track_username", "track_id", "username"),
    )

    def __repr__(self) -> str:
        return f"<PlayEvent(user={self.username}, track={self.title[:30]})>"
