"""Track management API endpoints.

Provides read and delete operations for tracks in the music library.
All endpoints require admin authentication.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app.api.deps import AdminToken, DbSession
from app.config import get_settings
from app.models.track import Track

router = APIRouter(prefix="/tracks", tags=["tracks"])


# --- Pydantic schemas ---


class TrackResponse(BaseModel):
    """Response schema for a track."""

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
    created_at: str
    updated_at: str


class TrackListResponse(BaseModel):
    """Paginated list of tracks."""

    items: list[TrackResponse]
    total: int
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=100)


class TrackDeleteResponse(BaseModel):
    """Response schema for track deletion."""

    id: uuid.UUID
    message: str
    file_removed: bool


# --- Endpoints ---


@router.get("", response_model=TrackListResponse)
async def list_tracks(
    db: DbSession,
    _token: AdminToken,
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
    search: str | None = Query(
        default=None,
        max_length=256,
        description="Search query (matches title, artist, or album)",
    ),
) -> TrackListResponse:
    """List tracks with pagination and optional text search.

    Search matches against title, artist, and album fields using
    case-insensitive substring matching. Results are ordered by
    creation date (newest first).
    """
    query = select(Track).order_by(Track.created_at.desc())

    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(
                Track.title.ilike(search_term),
                Track.artist.ilike(search_term),
                Track.album.ilike(search_term),
            )
        )

    # Get total count with the same filters
    count_query = select(func.count()).select_from(Track)
    if search:
        search_term = f"%{search}%"
        count_query = count_query.where(
            or_(
                Track.title.ilike(search_term),
                Track.artist.ilike(search_term),
                Track.album.ilike(search_term),
            )
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    tracks = result.scalars().all()

    return TrackListResponse(
        items=[
            TrackResponse(
                id=track.id,
                title=track.title,
                artist=track.artist,
                album=track.album,
                duration_seconds=track.duration_seconds,
                isrc=track.isrc,
                spotify_id=track.spotify_id,
                apple_music_id=track.apple_music_id,
                musicbrainz_id=track.musicbrainz_id,
                file_path=track.file_path,
                created_at=track.created_at.isoformat(),
                updated_at=track.updated_at.isoformat(),
            )
            for track in tracks
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{track_id}", response_model=TrackResponse)
async def get_track(
    track_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> TrackResponse:
    """Get detailed information about a specific track."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()

    if track is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Track {track_id} not found",
        )

    return TrackResponse(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        isrc=track.isrc,
        spotify_id=track.spotify_id,
        apple_music_id=track.apple_music_id,
        musicbrainz_id=track.musicbrainz_id,
        file_path=track.file_path,
        created_at=track.created_at.isoformat(),
        updated_at=track.updated_at.isoformat(),
    )


@router.delete("/{track_id}", response_model=TrackDeleteResponse)
async def delete_track(
    track_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> TrackDeleteResponse:
    """Delete a track from the database and remove its file from disk.

    If the track has an associated file, it will be deleted from the
    filesystem. The file deletion is best-effort -- the database record
    is removed even if the file cannot be deleted (e.g., already missing).
    """
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()

    if track is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Track {track_id} not found",
        )

    file_removed = False

    # Attempt to remove the file from disk
    if track.file_path:
        file_path = Path(track.file_path)
        settings = get_settings()

        # Safety check: only delete files within the music base path
        try:
            resolved = file_path.resolve()
            resolved.relative_to(settings.music_base_path.resolve())

            if resolved.exists():
                resolved.unlink()
                file_removed = True
        except (ValueError, OSError):
            # ValueError: path traversal attempt (relative_to fails)
            # OSError: file system error during unlink
            # In both cases, proceed with DB deletion
            pass

    # Remove from database
    await db.delete(track)
    await db.flush()

    return TrackDeleteResponse(
        id=track_id,
        message="Track deleted successfully",
        file_removed=file_removed,
    )
