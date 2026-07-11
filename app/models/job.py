"""Job model - represents a music acquisition request flowing through the pipeline."""

import enum
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDString

if TYPE_CHECKING:
    from app.models.candidate import Candidate
    from app.models.track import Track


class SourcePlatform(enum.StrEnum):
    """Platform the original link came from."""

    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"
    UNKNOWN = "unknown"


class JobStatus(enum.StrEnum):
    """Status of a job as it moves through the acquisition pipeline."""

    PENDING = "pending"
    RESOLVING = "resolving"
    SEARCHING = "searching"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(TimestampMixin, Base):
    """A music acquisition job triggered by a Mattermost link share."""

    __tablename__ = "jobs"

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_platform: Mapped[SourcePlatform] = mapped_column(
        Enum(SourcePlatform, name="source_platform"),
        nullable=False,
        default=SourcePlatform.UNKNOWN,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"),
        nullable=False,
        default=JobStatus.PENDING,
    )

    # Mattermost context
    mattermost_post_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    mattermost_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requester_user_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Resolved metadata
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artist: Mapped[str | None] = mapped_column(String(512), nullable=True)
    album: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Error handling
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationship to the final track (set when job completes)
    track_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString(),
        ForeignKey("tracks.id", ondelete="SET NULL"),
        nullable=True,
    )
    track: Mapped[Optional["Track"]] = relationship(
        "Track",
        lazy="selectin",
    )

    # Relationship to candidates found during search
    candidates: Mapped[list["Candidate"]] = relationship(
        "Candidate",
        back_populates="job",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Job(id={self.id}, status={self.status.value}, url='{self.url[:50]}')>"
