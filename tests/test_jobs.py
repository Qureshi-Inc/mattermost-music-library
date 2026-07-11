"""Tests for job pipeline - creation, status transitions, retry logic, recovery."""


import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.job import Job, JobStatus, SourcePlatform


class TestJobCreation:
    """Test creating jobs in the pipeline."""

    @pytest.mark.asyncio
    async def test_create_job_from_spotify_url(self, db_session: AsyncSession):
        """A job is created from a Spotify URL with correct initial state."""
        job = Job(
            url="https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.PENDING,
            mattermost_post_id="post123",
            mattermost_channel_id="channel456",
            requester_user_id="user789",
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.PENDING
        assert fetched.source_platform == SourcePlatform.SPOTIFY
        assert fetched.retry_count == 0
        assert fetched.error_message is None

    @pytest.mark.asyncio
    async def test_create_job_from_youtube_url(self, db_session: AsyncSession):
        """A job is created from a YouTube URL."""
        job = Job(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_platform=SourcePlatform.YOUTUBE,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.source_platform == SourcePlatform.YOUTUBE

    @pytest.mark.asyncio
    async def test_create_job_from_apple_music_url(self, db_session: AsyncSession):
        """A job is created from an Apple Music URL."""
        job = Job(
            url="https://music.apple.com/us/album/bohemian-rhapsody/1440806041",
            source_platform=SourcePlatform.APPLE_MUSIC,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.source_platform == SourcePlatform.APPLE_MUSIC


class TestJobStatusTransitions:
    """Test job status transitions through the pipeline."""

    @pytest.mark.asyncio
    async def test_pending_to_resolving(self, db_session: AsyncSession):
        """Job transitions from PENDING to RESOLVING."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.RESOLVING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.RESOLVING

    @pytest.mark.asyncio
    async def test_resolving_to_searching(self, db_session: AsyncSession):
        """Job transitions from RESOLVING to SEARCHING."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.RESOLVING,
            title="Bohemian Rhapsody",
            artist="Queen",
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.SEARCHING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.SEARCHING

    @pytest.mark.asyncio
    async def test_searching_to_reviewing(self, db_session: AsyncSession):
        """Job transitions from SEARCHING to REVIEWING after candidates found."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.SEARCHING,
        )
        db_session.add(job)
        await db_session.flush()

        # Add candidates
        candidate = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=xyz",
            youtube_id="xyz",
            title="Match",
            score=0.85,
            selected=False,
        )
        db_session.add(candidate)
        job.status = JobStatus.REVIEWING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.REVIEWING
        assert len(fetched.candidates) == 1

    @pytest.mark.asyncio
    async def test_reviewing_to_approved(self, db_session: AsyncSession):
        """Job transitions from REVIEWING to APPROVED."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.REVIEWING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.APPROVED
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.APPROVED

    @pytest.mark.asyncio
    async def test_approved_to_downloading(self, db_session: AsyncSession):
        """Job transitions from APPROVED to DOWNLOADING."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.APPROVED,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.DOWNLOADING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.DOWNLOADING

    @pytest.mark.asyncio
    async def test_downloading_to_processing(self, db_session: AsyncSession):
        """Job transitions from DOWNLOADING to PROCESSING."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.DOWNLOADING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.PROCESSING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.PROCESSING

    @pytest.mark.asyncio
    async def test_processing_to_complete(self, db_session: AsyncSession):
        """Job transitions from PROCESSING to COMPLETE."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.PROCESSING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.COMPLETE
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_full_happy_path(self, db_session: AsyncSession):
        """Job completes the full pipeline: PENDING -> ... -> COMPLETE."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        # Walk through the full pipeline
        transitions = [
            JobStatus.RESOLVING,
            JobStatus.SEARCHING,
            JobStatus.REVIEWING,
            JobStatus.APPROVED,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
            JobStatus.COMPLETE,
        ]
        for status in transitions:
            job.status = status
            await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_transition_to_cancelled(self, db_session: AsyncSession):
        """Job can be cancelled from any state."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.REVIEWING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.CANCELLED
        await db_session.commit()

        result = await db_session.execute(select(Job))
        assert result.scalar_one().status == JobStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_transition_to_failed(self, db_session: AsyncSession):
        """Job can fail from any state with error message."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.DOWNLOADING,
        )
        db_session.add(job)
        await db_session.commit()

        job.status = JobStatus.FAILED
        job.error_message = "Download timed out after 60 seconds"
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.FAILED
        assert "timed out" in fetched.error_message


class TestRetryLogic:
    """Test job retry behavior."""

    @pytest.mark.asyncio
    async def test_retry_increments_count(self, db_session: AsyncSession):
        """Retrying a job increments the retry_count."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.FAILED,
            error_message="Temporary network error",
            retry_count=0,
        )
        db_session.add(job)
        await db_session.commit()

        # Simulate retry: increment count, clear error, reset to PENDING
        job.retry_count += 1
        job.error_message = None
        job.status = JobStatus.PENDING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.retry_count == 1
        assert fetched.status == JobStatus.PENDING
        assert fetched.error_message is None

    @pytest.mark.asyncio
    async def test_multiple_retries(self, db_session: AsyncSession):
        """Multiple retries accumulate in retry_count."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.FAILED,
            retry_count=0,
        )
        db_session.add(job)
        await db_session.commit()

        # Simulate 3 retries
        for i in range(3):
            job.retry_count += 1
            job.status = JobStatus.PENDING
            await db_session.commit()
            # Simulate failure again
            job.status = JobStatus.FAILED
            job.error_message = f"Attempt {i + 1} failed"
            await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.retry_count == 3
        assert fetched.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_retry_preserves_metadata(self, db_session: AsyncSession):
        """Retrying preserves the resolved metadata (title, artist, album)."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.FAILED,
            title="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            retry_count=1,
        )
        db_session.add(job)
        await db_session.commit()

        # Retry: go back to SEARCHING (skip resolving since we already have metadata)
        job.status = JobStatus.SEARCHING
        job.retry_count += 1
        job.error_message = None
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.title == "Bohemian Rhapsody"
        assert fetched.artist == "Queen"
        assert fetched.album == "A Night at the Opera"


class TestStartupRecovery:
    """Test job recovery on application startup."""

    @pytest.mark.asyncio
    async def test_find_stalled_jobs(self, db_session: AsyncSession):
        """Jobs stuck in active states can be found for recovery."""
        # Create jobs in various states
        pending = Job(url="http://a.com/1", source_platform=SourcePlatform.UNKNOWN, status=JobStatus.PENDING)
        resolving = Job(url="http://a.com/2", source_platform=SourcePlatform.UNKNOWN, status=JobStatus.RESOLVING)
        downloading = Job(url="http://a.com/3", source_platform=SourcePlatform.UNKNOWN, status=JobStatus.DOWNLOADING)
        complete = Job(url="http://a.com/4", source_platform=SourcePlatform.UNKNOWN, status=JobStatus.COMPLETE)
        failed = Job(url="http://a.com/5", source_platform=SourcePlatform.UNKNOWN, status=JobStatus.FAILED)

        db_session.add_all([pending, resolving, downloading, complete, failed])
        await db_session.commit()

        # Query for active (stalled) jobs that need recovery
        active_statuses = [
            JobStatus.RESOLVING,
            JobStatus.SEARCHING,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
        ]
        result = await db_session.execute(
            select(Job).where(Job.status.in_(active_statuses))
        )
        stalled = result.scalars().all()

        # Should find resolving and downloading (active states)
        assert len(stalled) == 2
        statuses = {j.status for j in stalled}
        assert JobStatus.RESOLVING in statuses
        assert JobStatus.DOWNLOADING in statuses

    @pytest.mark.asyncio
    async def test_recover_stalled_job_to_pending(self, db_session: AsyncSession):
        """Stalled jobs are reset to PENDING for retry on recovery."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.DOWNLOADING,
            retry_count=0,
        )
        db_session.add(job)
        await db_session.commit()

        # Recovery logic: reset stalled jobs
        job.status = JobStatus.PENDING
        job.error_message = "Recovered from stalled state on startup"
        job.retry_count += 1
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.PENDING
        assert "Recovered" in fetched.error_message
        assert fetched.retry_count == 1

    @pytest.mark.asyncio
    async def test_completed_jobs_not_recovered(self, db_session: AsyncSession):
        """Completed and cancelled jobs are not touched during recovery."""
        complete = Job(
            url="http://a.com/1",
            source_platform=SourcePlatform.UNKNOWN,
            status=JobStatus.COMPLETE,
        )
        cancelled = Job(
            url="http://a.com/2",
            source_platform=SourcePlatform.UNKNOWN,
            status=JobStatus.CANCELLED,
        )
        db_session.add_all([complete, cancelled])
        await db_session.commit()

        # Recovery query should not find these
        active_statuses = [
            JobStatus.RESOLVING,
            JobStatus.SEARCHING,
            JobStatus.DOWNLOADING,
            JobStatus.PROCESSING,
        ]
        result = await db_session.execute(
            select(Job).where(Job.status.in_(active_statuses))
        )
        stalled = result.scalars().all()
        assert len(stalled) == 0


class TestJobWithCandidates:
    """Test job-candidate pipeline interactions."""

    @pytest.mark.asyncio
    async def test_auto_approve_high_score_candidate(self, db_session: AsyncSession):
        """A candidate with score >= auto_approve_threshold triggers auto-approval."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.SEARCHING,
        )
        db_session.add(job)
        await db_session.flush()

        candidate = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=xyz",
            youtube_id="xyz",
            title="Perfect Match",
            score=0.95,  # Above 0.90 threshold
            selected=False,
        )
        db_session.add(candidate)
        await db_session.commit()

        # Simulate auto-approval logic
        auto_approve_threshold = 0.90
        if candidate.score >= auto_approve_threshold:
            candidate.selected = True
            job.status = JobStatus.APPROVED
            await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.APPROVED
        assert fetched.candidates[0].selected is True

    @pytest.mark.asyncio
    async def test_manual_review_for_low_score(self, db_session: AsyncSession):
        """Candidates below manual_review_threshold require manual review."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.SEARCHING,
        )
        db_session.add(job)
        await db_session.flush()

        candidate = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=xyz",
            youtube_id="xyz",
            title="Questionable Match",
            score=0.65,  # Below 0.70 threshold
            selected=False,
        )
        db_session.add(candidate)
        await db_session.commit()

        # Logic: score < manual_review_threshold -> stay in REVIEWING
        manual_review_threshold = 0.70
        if candidate.score < manual_review_threshold:
            job.status = JobStatus.REVIEWING
            await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.REVIEWING
        assert fetched.candidates[0].selected is False
