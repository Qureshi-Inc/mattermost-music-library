"""Track model - represents an audio track in the music library."""


from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Track(TimestampMixin, Base):
    """A music track stored in the library (e.g. on Jellyfin)."""

    __tablename__ = "tracks"

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    artist: Mapped[str] = mapped_column(String(512), nullable=False)
    album: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # External identifiers for cross-platform matching
    isrc: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    spotify_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    apple_music_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    musicbrainz_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Local storage
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        Index("ix_tracks_artist_title", "artist", "title"),
    )

    def __repr__(self) -> str:
        return f"<Track(id={self.id}, title='{self.title}', artist='{self.artist}')>"
