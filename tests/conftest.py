"""Shared pytest fixtures for Slaptastic test suite."""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Test configuration values
# ---------------------------------------------------------------------------

TEST_CONFIG = {
    "MATTERMOST_URL": "http://localhost:8065",
    "MATTERMOST_TOKEN": "test-token",
    "MATTERMOST_CHANNEL": "test-channel",
    "BOT_USERNAME": "slaptastic",
    "ADMIN_API_TOKEN": "test-admin-token",
    "MUSIC_BASE_PATH": "/tmp/test-music",
    "DB_URL": "sqlite+aiosqlite:///:memory:",
    "DOWNLOAD_ENABLED": "true",
    "SPOTIFY_CLIENT_ID": "test-spotify-id",
    "SPOTIFY_CLIENT_SECRET": "test-spotify-secret",
    "APPLE_MUSIC_TOKEN": "test-apple-token",
    "JELLYFIN_URL": "http://localhost:8096",
    "JELLYFIN_TOKEN": "test-jellyfin-token",
}


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Set environment variables for all tests."""
    for key, value in TEST_CONFIG.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Import all models so metadata is populated
    from app.models import Base  # noqa: F401 - imports Job, Track, Candidate via __init__

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async SQLAlchemy session connected to in-memory SQLite."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# FastAPI / httpx fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client():
    """Create an httpx AsyncClient with the FastAPI test application.

    Patches the database dependency to use in-memory SQLite.
    """
    from httpx import ASGITransport, AsyncClient

    # Create a fresh engine for this test
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    from app.models import Base  # noqa: F401 - imports all models

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Import the app and override dependencies
    from app.database import get_db
    from app.main import app

    # Override both the original get_db and also patch check_db_connectivity
    app.dependency_overrides[get_db] = _override_get_db

    with patch("app.main._start_mattermost_listener", return_value=None), \
         patch("app.database.check_db_connectivity", return_value=True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Mock config fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings():
    """Return a Settings instance with test values."""
    from app.config import Settings

    return Settings(
        mattermost_url="http://localhost:8065",
        mattermost_token="test-token",
        mattermost_channel="test-channel",
        bot_username="slaptastic",
        admin_api_token="test-admin-token",
        music_base_path=Path("/tmp/test-music"),
        db_url="sqlite+aiosqlite:///:memory:",
        download_enabled=True,
        spotify_client_id="test-spotify-id",
        spotify_client_secret="test-spotify-secret",
        apple_music_token="test-apple-token",
    )


# ---------------------------------------------------------------------------
# Data factories
# ---------------------------------------------------------------------------


@pytest.fixture
def track_factory():
    """Factory for creating Track model instances with sensible defaults."""

    def _make_track(**kwargs):
        from app.models.track import Track

        defaults = {
            "id": uuid.uuid4(),
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "A Night at the Opera",
            "duration_seconds": 354,
            "isrc": "GBUM71029604",
            "spotify_id": "4u7EnebtmKWzUH433cf5Qv",
            "file_path": "/music/Queen/A Night at the Opera/01 - Bohemian Rhapsody.mp3",
        }
        defaults.update(kwargs)
        return Track(**defaults)

    return _make_track


@pytest.fixture
def job_factory():
    """Factory for creating Job model instances with sensible defaults."""

    def _make_job(**kwargs):
        from app.models.job import Job, JobStatus, SourcePlatform

        defaults = {
            "id": uuid.uuid4(),
            "url": "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv",
            "source_platform": SourcePlatform.SPOTIFY,
            "status": JobStatus.PENDING,
            "mattermost_post_id": "post123",
            "mattermost_channel_id": "test-channel",
            "requester_user_id": "user456",
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "A Night at the Opera",
            "retry_count": 0,
        }
        defaults.update(kwargs)
        return Job(**defaults)

    return _make_job


@pytest.fixture
def candidate_factory():
    """Factory for creating Candidate model instances with sensible defaults."""

    def _make_candidate(job_id=None, **kwargs):
        from app.models.candidate import Candidate

        defaults = {
            "id": uuid.uuid4(),
            "job_id": job_id or uuid.uuid4(),
            "youtube_url": "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
            "youtube_id": "fJ9rUzIMcZQ",
            "title": "Queen - Bohemian Rhapsody (Official Video)",
            "channel": "Queen Official",
            "duration_seconds": 354,
            "view_count": 1_600_000_000,
            "score": 0.95,
            "selected": False,
        }
        defaults.update(kwargs)
        return Candidate(**defaults)

    return _make_candidate
