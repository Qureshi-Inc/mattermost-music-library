"""Pydantic schemas for Track model."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TrackCreate(BaseModel):
    """Schema for creating a new track."""

    title: str = Field(..., max_length=512)
    artist: str = Field(..., max_length=512)
    album: str | None = Field(None, max_length=512)
    duration_seconds: int | None = Field(None, ge=0)
    isrc: str | None = Field(None, max_length=12)
    spotify_id: str | None = Field(None, max_length=64)
    apple_music_id: str | None = Field(None, max_length=64)
    musicbrainz_id: str | None = Field(None, max_length=64)
    file_path: str | None = Field(None, max_length=1024)


class TrackResponse(BaseModel):
    """Schema for track responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    artist: str
    album: str | None = None
    duration_seconds: int | None = None
    isrc: str | None = None
    spotify_id: str | None = None
    apple_music_id: str | None = None
    musicbrainz_id: str | None = None
    file_path: str | None = None
    created_at: datetime
    updated_at: datetime


class TrackList(BaseModel):
    """Schema for paginated track list responses."""

    items: list[TrackResponse]
    total: int
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=100)
