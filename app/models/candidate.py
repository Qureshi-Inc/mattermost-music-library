"""Candidate model - a potential YouTube download match for a job."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDString

if TYPE_CHECKING:
    from app.models.job import Job


class Candidate(TimestampMixin, Base):
    """A YouTube video candidate considered for downloading to fulfill a job."""

    __tablename__ = "candidates"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString(),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    # YouTube metadata
    youtube_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    youtube_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(256), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Scoring and selection
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Relationship back to parent job
    job: Mapped["Job"] = relationship(
        "Job",
        back_populates="candidates",
    )

    __table_args__ = (
        Index("ix_candidates_job_id", "job_id"),
        Index("ix_candidates_selected", "job_id", "selected"),
    )

    def __repr__(self) -> str:
        return f"<Candidate(id={self.id}, youtube_id='{self.youtube_id}', score={self.score})>"
