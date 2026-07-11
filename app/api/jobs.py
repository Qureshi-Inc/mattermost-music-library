"""Job management API endpoints.

Provides CRUD and lifecycle operations for music acquisition jobs:
listing, creating, approving, cancelling, and retrying jobs.
All endpoints require admin authentication.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminToken, DbSession
from app.models.job import Job, JobStatus, SourcePlatform
from app.security.validation import ValidationError, validate_music_url

router = APIRouter(prefix="/jobs", tags=["jobs"])


# --- Pydantic schemas for requests/responses ---


class CandidateResponse(BaseModel):
    """Response schema for a job candidate."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    youtube_url: str
    youtube_id: str
    title: str
    channel: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    score: float | None = None
    selected: bool = False
    rejected_reason: str | None = None
    created_at: str  # ISO format datetime


class JobResponse(BaseModel):
    """Response schema for a job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    source_platform: SourcePlatform
    status: JobStatus
    mattermost_post_id: str | None = None
    mattermost_channel_id: str | None = None
    requester_user_id: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    track_id: uuid.UUID | None = None
    created_at: str
    updated_at: str


class JobDetailResponse(JobResponse):
    """Response schema for a job with its candidates."""

    candidates: list[CandidateResponse] = []


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    items: list[JobResponse]
    total: int
    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=100)


class JobCreateRequest(BaseModel):
    """Request schema for creating a job manually."""

    url: str = Field(..., max_length=2048, description="Music URL to process")


class JobActionResponse(BaseModel):
    """Response schema for job action endpoints (approve, cancel, retry)."""

    id: uuid.UUID
    status: JobStatus
    message: str


# --- Helper to detect source platform from URL ---


def _detect_platform(url: str) -> SourcePlatform:
    """Detect the source platform from a URL."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return SourcePlatform.YOUTUBE
    elif "spotify.com" in url_lower:
        return SourcePlatform.SPOTIFY
    elif "music.apple.com" in url_lower:
        return SourcePlatform.APPLE_MUSIC
    return SourcePlatform.UNKNOWN


# --- Endpoints ---


@router.get("", response_model=JobListResponse)
async def list_jobs(
    db: DbSession,
    _token: AdminToken,
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
    status_filter: JobStatus | None = Query(  # noqa: B008
        default=None, alias="status", description="Filter by job status"
    ),
) -> JobListResponse:
    """List all jobs with pagination and optional status filtering.

    Returns jobs ordered by creation date (newest first).
    """
    # Build base query
    query = select(Job).order_by(Job.created_at.desc())

    if status_filter is not None:
        query = query.where(Job.status == status_filter)

    # Get total count
    count_query = select(func.count()).select_from(Job)
    if status_filter is not None:
        count_query = count_query.where(Job.status == status_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return JobListResponse(
        items=[
            JobResponse(
                id=job.id,
                url=job.url,
                source_platform=job.source_platform,
                status=job.status,
                mattermost_post_id=job.mattermost_post_id,
                mattermost_channel_id=job.mattermost_channel_id,
                requester_user_id=job.requester_user_id,
                title=job.title,
                artist=job.artist,
                album=job.album,
                error_message=job.error_message,
                retry_count=job.retry_count,
                track_id=job.track_id,
                created_at=job.created_at.isoformat(),
                updated_at=job.updated_at.isoformat(),
            )
            for job in jobs
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> JobDetailResponse:
    """Get detailed information about a specific job, including its candidates."""
    query = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.candidates))
    )
    result = await db.execute(query)
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    return JobDetailResponse(
        id=job.id,
        url=job.url,
        source_platform=job.source_platform,
        status=job.status,
        mattermost_post_id=job.mattermost_post_id,
        mattermost_channel_id=job.mattermost_channel_id,
        requester_user_id=job.requester_user_id,
        title=job.title,
        artist=job.artist,
        album=job.album,
        error_message=job.error_message,
        retry_count=job.retry_count,
        track_id=job.track_id,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
        candidates=[
            CandidateResponse(
                id=c.id,
                youtube_url=c.youtube_url,
                youtube_id=c.youtube_id,
                title=c.title,
                channel=c.channel,
                duration_seconds=c.duration_seconds,
                view_count=c.view_count,
                score=c.score,
                selected=c.selected,
                rejected_reason=c.rejected_reason,
                created_at=c.created_at.isoformat(),
            )
            for c in job.candidates
        ],
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: JobCreateRequest,
    db: DbSession,
    _token: AdminToken,
) -> JobResponse:
    """Create a new music acquisition job by submitting a URL.

    The URL is validated against the allowed music platform domains before
    the job is created.
    """
    # Validate the URL against allowed domains
    try:
        validated_url = validate_music_url(request.url)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        ) from exc

    # Detect source platform
    platform = _detect_platform(validated_url)

    # Create the job
    job = Job(
        url=validated_url,
        source_platform=platform,
        status=JobStatus.PENDING,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    return JobResponse(
        id=job.id,
        url=job.url,
        source_platform=job.source_platform,
        status=job.status,
        mattermost_post_id=job.mattermost_post_id,
        mattermost_channel_id=job.mattermost_channel_id,
        requester_user_id=job.requester_user_id,
        title=job.title,
        artist=job.artist,
        album=job.album,
        error_message=job.error_message,
        retry_count=job.retry_count,
        track_id=job.track_id,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


@router.post("/{job_id}/approve", response_model=JobActionResponse)
async def approve_job(
    job_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> JobActionResponse:
    """Approve a job for download.

    Only jobs in REVIEWING status can be approved. Transitions the job to
    APPROVED status, making it eligible for the download pipeline.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    if job.status != JobStatus.REVIEWING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve job in '{job.status.value}' status -- must be 'reviewing'",
        )

    job.status = JobStatus.APPROVED
    await db.flush()
    await db.refresh(job)

    return JobActionResponse(
        id=job.id,
        status=job.status,
        message="Job approved for download",
    )


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(
    job_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> JobActionResponse:
    """Cancel a job.

    Jobs that are already COMPLETE, CANCELLED, or actively DOWNLOADING cannot
    be cancelled.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    non_cancellable = {JobStatus.COMPLETE, JobStatus.CANCELLED, JobStatus.DOWNLOADING}
    if job.status in non_cancellable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in '{job.status.value}' status",
        )

    job.status = JobStatus.CANCELLED
    await db.flush()
    await db.refresh(job)

    return JobActionResponse(
        id=job.id,
        status=job.status,
        message="Job cancelled",
    )


@router.post("/{job_id}/retry", response_model=JobActionResponse)
async def retry_job(
    job_id: uuid.UUID,
    db: DbSession,
    _token: AdminToken,
) -> JobActionResponse:
    """Retry a failed job.

    Only jobs in FAILED status can be retried. Resets the job to PENDING
    status, clears the error message, and increments the retry counter.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry job in '{job.status.value}' status -- must be 'failed'",
        )

    job.status = JobStatus.PENDING
    job.error_message = None
    job.retry_count += 1
    await db.flush()
    await db.refresh(job)

    return JobActionResponse(
        id=job.id,
        status=job.status,
        message=f"Job queued for retry (attempt #{job.retry_count})",
    )
