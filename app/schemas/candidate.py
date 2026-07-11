"""Pydantic schemas for Candidate model."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CandidateResponse(BaseModel):
    """Schema for candidate responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    youtube_url: str
    youtube_id: str
    title: str
    channel: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    score: float | None = None
    selected: bool
    rejected_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class CandidateList(BaseModel):
    """Schema for paginated candidate list responses."""

    items: list[CandidateResponse]
    total: int
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=100)
