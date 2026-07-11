"""Tests for app.models - model creation, relationships, and constraints."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate
from app.models.job import Job, JobStatus, SourcePlatform
from app.models.track import Track


class TestTrackModel:
    """Test the Track model."""

    @pytest.mark.asyncio
    async def test_create_track(self, db_session: AsyncSession):
        """A track can be created and persisted."""
        track = Track(
            title="Stairway to Heaven",
            artist="Led Zeppelin",
            album="Led Zeppelin IV",
            duration_seconds=482,
            isrc="USAT29900609",
        )
        db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(select(Track).where(Track.title == "Stairway to Heaven"))
        fetched = result.scalar_one()
        assert fetched.artist == "Led Zeppelin"
        assert fetched.duration_seconds == 482
        assert fetched.isrc == "USAT29900609"

    @pytest.mark.asyncio
    async def test_track_has_uuid_id(self, db_session: AsyncSession):
        """Track id is a UUID."""
        track = Track(title="Test", artist="Artist")
        db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(select(Track))
        fetched = result.scalar_one()
        assert isinstance(fetched.id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_track_created_at_set_automatically(self, db_session: AsyncSession):
        """Track created_at is populated on insert."""
        track = Track(title="Test", artist="Artist")
        db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(select(Track))
        fetched = result.scalar_one()
        assert fetched.created_at is not None

    @pytest.mark.asyncio
    async def test_track_nullable_fields(self, db_session: AsyncSession):
        """Track optional fields can be None."""
        track = Track(title="Minimal", artist="Artist")
        db_session.add(track)
        await db_session.commit()

        result = await db_session.execute(select(Track))
        fetched = result.scalar_one()
        assert fetched.album is None
        assert fetched.duration_seconds is None
        assert fetched.isrc is None
        assert fetched.spotify_id is None
        assert fetched.file_path is None

    @pytest.mark.asyncio
    async def test_track_repr(self, track_factory):
        """Track __repr__ includes title and artist."""
        track = track_factory(title="Song", artist="Band")
        repr_str = repr(track)
        assert "Song" in repr_str
        assert "Band" in repr_str


class TestJobModel:
    """Test the Job model."""

    @pytest.mark.asyncio
    async def test_create_job(self, db_session: AsyncSession):
        """A job can be created with pending status."""
        job = Job(
            url="https://open.spotify.com/track/abc123",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.url == "https://open.spotify.com/track/abc123"
        assert fetched.status == JobStatus.PENDING
        assert fetched.source_platform == SourcePlatform.SPOTIFY

    @pytest.mark.asyncio
    async def test_job_status_transitions(self, db_session: AsyncSession):
        """Job status can be updated through the pipeline."""
        job = Job(
            url="https://youtu.be/abc",
            source_platform=SourcePlatform.YOUTUBE,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        # Transition to resolving
        job.status = JobStatus.RESOLVING
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.status == JobStatus.RESOLVING

    @pytest.mark.asyncio
    async def test_job_retry_count_default(self, db_session: AsyncSession):
        """Job retry_count defaults to 0."""
        job = Job(
            url="https://youtu.be/abc",
            source_platform=SourcePlatform.YOUTUBE,
            status=JobStatus.PENDING,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.retry_count == 0

    @pytest.mark.asyncio
    async def test_job_with_error_message(self, db_session: AsyncSession):
        """Job can store error messages when failed."""
        job = Job(
            url="https://youtu.be/abc",
            source_platform=SourcePlatform.YOUTUBE,
            status=JobStatus.FAILED,
            error_message="Download failed: geo-restricted",
            retry_count=3,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched = result.scalar_one()
        assert fetched.error_message == "Download failed: geo-restricted"
        assert fetched.retry_count == 3

    @pytest.mark.asyncio
    async def test_job_source_platform_enum(self):
        """SourcePlatform enum has expected values."""
        assert SourcePlatform.YOUTUBE.value == "youtube"
        assert SourcePlatform.SPOTIFY.value == "spotify"
        assert SourcePlatform.APPLE_MUSIC.value == "apple_music"
        assert SourcePlatform.UNKNOWN.value == "unknown"

    @pytest.mark.asyncio
    async def test_job_status_enum_all_values(self):
        """JobStatus enum contains all pipeline stages."""
        expected_statuses = {
            "pending", "resolving", "searching", "reviewing",
            "approved", "downloading", "processing", "complete",
            "failed", "cancelled",
        }
        actual_statuses = {s.value for s in JobStatus}
        assert actual_statuses == expected_statuses


class TestCandidateModel:
    """Test the Candidate model."""

    @pytest.mark.asyncio
    async def test_create_candidate(self, db_session: AsyncSession):
        """A candidate can be created and linked to a job."""
        # Create parent job first
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.SEARCHING,
        )
        db_session.add(job)
        await db_session.flush()

        candidate = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            youtube_id="dQw4w9WgXcQ",
            title="Rick Astley - Never Gonna Give You Up",
            channel="Rick Astley",
            duration_seconds=213,
            view_count=1_500_000_000,
            score=0.92,
            selected=False,
        )
        db_session.add(candidate)
        await db_session.commit()

        result = await db_session.execute(select(Candidate))
        fetched = result.scalar_one()
        assert fetched.youtube_id == "dQw4w9WgXcQ"
        assert fetched.score == 0.92
        assert fetched.selected is False

    @pytest.mark.asyncio
    async def test_candidate_job_relationship(self, db_session: AsyncSession):
        """Candidates are accessible through the job relationship."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.SEARCHING,
        )
        db_session.add(job)
        await db_session.flush()

        c1 = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=aaa",
            youtube_id="aaa",
            title="Candidate 1",
            score=0.9,
            selected=False,
        )
        c2 = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=bbb",
            youtube_id="bbb",
            title="Candidate 2",
            score=0.8,
            selected=False,
        )
        db_session.add_all([c1, c2])
        await db_session.commit()

        # Refresh to load relationships
        result = await db_session.execute(select(Job))
        fetched_job = result.scalar_one()
        assert len(fetched_job.candidates) == 2

    @pytest.mark.asyncio
    async def test_candidate_selected_field(self, db_session: AsyncSession):
        """Candidate selected can be toggled."""
        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.REVIEWING,
        )
        db_session.add(job)
        await db_session.flush()

        candidate = Candidate(
            job_id=job.id,
            youtube_url="https://www.youtube.com/watch?v=xyz",
            youtube_id="xyz",
            title="Test Candidate",
            score=0.85,
            selected=False,
        )
        db_session.add(candidate)
        await db_session.commit()

        candidate.selected = True
        await db_session.commit()

        result = await db_session.execute(select(Candidate))
        fetched = result.scalar_one()
        assert fetched.selected is True


class TestJobTrackRelationship:
    """Test the Job -> Track relationship."""

    @pytest.mark.asyncio
    async def test_job_links_to_track_on_completion(self, db_session: AsyncSession):
        """A completed job can be linked to a track."""
        track = Track(
            title="Test Song",
            artist="Test Artist",
            file_path="/music/Test Artist/Album/01 - Test Song.mp3",
        )
        db_session.add(track)
        await db_session.flush()

        job = Job(
            url="https://open.spotify.com/track/abc",
            source_platform=SourcePlatform.SPOTIFY,
            status=JobStatus.COMPLETE,
            track_id=track.id,
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job))
        fetched_job = result.scalar_one()
        assert fetched_job.track_id == track.id
