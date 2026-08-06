"""Model tracking which scrobbled tracks have been auto-added, to prevent
double-adding across polling passes and restarts."""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ScrobbleAdd(TimestampMixin, Base):
    """One row per (source, track) that the scrobble watcher has auto-added."""

    __tablename__ = "scrobble_adds"
    __table_args__ = (
        UniqueConstraint("source_name", "norm_key", name="uq_scrobble_source_track"),
    )

    # multi-scrobbler source component name (== the friend, by convention).
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Normalized "artist||track" dedup key.
    norm_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    track: Mapped[str | None] = mapped_column(String(512), nullable=True)
    play_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
