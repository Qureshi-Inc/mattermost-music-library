"""Job queue backed by SQLite via async SQLAlchemy.

Persists jobs across restarts and supports retry with exponential backoff.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.job import Job, JobStatus, SourcePlatform

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 30  # 30s, 60s, 120s


class JobQueue:
    """Manages job lifecycle in the database.

    Creates, queries, updates, and manages retry logic for music
    acquisition jobs. All operations use async SQLAlchemy sessions.

    Can optionally be initialized with a session for use within
    an existing transaction context.
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        """Initialize the job queue.

        Args:
            session: Optional pre-existing session. If not provided,
                     creates new sessions from the factory for each operation.
        """
        self._external_session = session

    async def create_job(
        self,
        url: str,
        source_platform: SourcePlatform = SourcePlatform.UNKNOWN,
        mattermost_post_id: str | None = None,
        mattermost_channel_id: str | None = None,
        requester_user_id: str | None = None,
    ) -> Job:
        """Create a new job from a music link submission.

        Args:
            url: The music URL to process.
            source_platform: Which platform the URL is from.
            mattermost_post_id: The Mattermost post that triggered this job.
            mattermost_channel_id: The channel the request came from.
            requester_user_id: The user who submitted the link.

        Returns:
            The newly created Job instance.
        """
        async with async_session_factory() as session:
            job = Job(
                url=url,
                source_platform=source_platform,
                status=JobStatus.PENDING,
                mattermost_post_id=mattermost_post_id,
                mattermost_channel_id=mattermost_channel_id,
                requester_user_id=requester_user_id,
                retry_count=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            logger.info(
                "Job created",
                extra={
                    "job_id": str(job.id),
                    "url": url,
                    "platform": source_platform.value,
                },
            )
            return job

    async def get_job(self, job_id: uuid.UUID) -> Job | None:
        """Retrieve a job by its ID.

        Args:
            job_id: The UUID of the job.

        Returns:
            The Job if found, None otherwise.
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == job_id)
            )
            return result.scalar_one_or_none()

    async def list_jobs(
        self,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """List jobs, optionally filtered by status.

        Args:
            status: If provided, only return jobs with this status.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip (for pagination).

        Returns:
            List of Job instances ordered by creation time (newest first).
        """
        async with async_session_factory() as session:
            query = select(Job).order_by(Job.created_at.desc())

            if status is not None:
                query = query.where(Job.status == status)

            query = query.limit(limit).offset(offset)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        error_message: str | None = None,
        title: str | None = None,
        artist: str | None = None,
        album: str | None = None,
        track_id: uuid.UUID | None = None,
    ) -> Job | None:
        """Update a job's status and optional metadata fields.

        Args:
            job_id: The job to update.
            status: The new status.
            error_message: Optional error message (set on failure).
            title: Optional resolved title.
            artist: Optional resolved artist.
            album: Optional resolved album.
            track_id: Optional track ID (set on completion).

        Returns:
            The updated Job, or None if not found.
        """
        async with async_session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                logger.warning("Job not found for status update", extra={"job_id": str(job_id)})
                return None

            job.status = status

            if error_message is not None:
                job.error_message = error_message
            if title is not None:
                job.title = title
            if artist is not None:
                job.artist = artist
            if album is not None:
                job.album = album
            if track_id is not None:
                job.track_id = track_id

            await session.commit()
            await session.refresh(job)

            logger.info(
                "Job status updated",
                extra={"job_id": str(job_id), "status": status.value},
            )
            return job

    async def cancel_job(self, job_id: uuid.UUID) -> Job | None:
        """Cancel a job if it is not already complete or cancelled.

        Args:
            job_id: The job to cancel.

        Returns:
            The updated Job, or None if not found or already terminal.
        """
        async with async_session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return None

            # Cannot cancel terminal states
            if job.status in (JobStatus.COMPLETE, JobStatus.CANCELLED):
                logger.info(
                    "Cannot cancel job in terminal state",
                    extra={"job_id": str(job_id), "status": job.status.value},
                )
                return job

            job.status = JobStatus.CANCELLED
            await session.commit()
            await session.refresh(job)

            logger.info("Job cancelled", extra={"job_id": str(job_id)})
            return job

    async def mark_failed(
        self, job_id: uuid.UUID, error_message: str
    ) -> Job | None:
        """Mark a job as failed, incrementing retry count.

        If the job has not exceeded MAX_RETRIES, it will be set back to
        PENDING for retry. Otherwise, it is marked as permanently FAILED.

        Args:
            job_id: The job that failed.
            error_message: Description of what went wrong.

        Returns:
            The updated Job with new status and retry count.
        """
        async with async_session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return None

            job.retry_count += 1
            job.error_message = error_message

            if job.retry_count >= MAX_RETRIES:
                job.status = JobStatus.FAILED
                logger.error(
                    "Job permanently failed after max retries",
                    extra={
                        "job_id": str(job_id),
                        "retries": job.retry_count,
                        "error": error_message,
                    },
                )
            else:
                job.status = JobStatus.PENDING
                logger.warning(
                    "Job marked for retry",
                    extra={
                        "job_id": str(job_id),
                        "retry_count": job.retry_count,
                        "error": error_message,
                    },
                )

            await session.commit()
            await session.refresh(job)
            return job

    async def get_pending_jobs(self) -> list[Job]:
        """Get all jobs in PENDING status, ordered by creation time.

        Returns:
            List of pending jobs ready for processing.
        """
        async with async_session_factory() as session:
            result = await session.execute(
                select(Job)
                .where(Job.status == JobStatus.PENDING)
                .order_by(Job.created_at.asc())
            )
            return list(result.scalars().all())

    async def get_retry_delay(self, job: Job) -> float:
        """Calculate exponential backoff delay for a job retry.

        Uses the formula: base_delay * 2^(retry_count - 1)
        So: 30s, 60s, 120s for retries 1, 2, 3.

        Args:
            job: The job to calculate delay for.

        Returns:
            Delay in seconds before the job should be retried.
        """
        if job.retry_count <= 0:
            return 0.0
        return float(BASE_BACKOFF_SECONDS * (2 ** (job.retry_count - 1)))

    async def get_in_progress_jobs(self) -> list[Job]:
        """Get all jobs that are currently in progress (not pending, not terminal).

        Returns:
            List of jobs in active processing states.
        """
        in_progress_statuses = [
            JobStatus.RESOLVING,
            JobStatus.SEARCHING,
            JobStatus.REVIEWING,
            JobStatus.APPROVED,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
        ]
        async with async_session_factory() as session:
            result = await session.execute(
                select(Job)
                .where(Job.status.in_(in_progress_statuses))
                .order_by(Job.created_at.asc())
            )
            return list(result.scalars().all())
