"""Pydantic schemas for Job model."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus as JobStatusEnum
from app.models.job import SourcePlatform
from app.schemas.candidate import CandidateResponse
from app.schemas.track import TrackResponse


class JobCreate(BaseModel):
    """Schema for creating a new job."""

    url: str = Field(..., max_length=2048)
    source_platform: SourcePlatform = SourcePlatform.UNKNOWN
    mattermost_post_id: str | None = Field(None, max_length=64)
    mattermost_channel_id: str | None = Field(None, max_length=64)
    requester_user_id: str | None = Field(None, max_length=64)
    title: str | None = Field(None, max_length=512)
    artist: str | None = Field(None, max_length=512)
    album: str | None = Field(None, max_length=512)


class JobStatus(BaseModel):
    """Schema for job status updates."""

    status: JobStatusEnum
    error_message: str | None = None


class JobResponse(BaseModel):
    """Schema for job responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    source_platform: SourcePlatform
    status: JobStatusEnum
    mattermost_post_id: str | None = None
    mattermost_channel_id: str | None = None
    requester_user_id: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    error_message: str | None = None
    retry_count: int
    track_id: uuid.UUID | None = None
    track: TrackResponse | None = None
    candidates: list[CandidateResponse] = []
    created_at: datetime
    updated_at: datetime


class JobList(BaseModel):
    """Schema for paginated job list responses."""

    items: list[JobResponse]
    total: int
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=100)
