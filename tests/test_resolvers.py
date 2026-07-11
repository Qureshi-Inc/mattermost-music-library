"""Tests for app.resolvers - Spotify, YouTube, and base resolver logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.resolvers.base import TrackMetadata


class TestTrackMetadata:
    """Test the TrackMetadata dataclass."""

    def test_has_minimum_with_title(self):
        """has_minimum is True when title is set."""
        meta = TrackMetadata(title="Song")
        assert meta.has_minimum is True

    def test_has_minimum_with_provider_id(self):
        """has_minimum is True when provider_id is set."""
        meta = TrackMetadata(provider_id="abc123")
        assert meta.has_minimum is True

    def test_has_minimum_empty(self):
        """has_minimum is False when both title and provider_id are None."""
        meta = TrackMetadata()
        assert meta.has_minimum is False

    def test_merge_fills_none_fields(self):
        """merge fills None fields from the other metadata."""
        primary = TrackMetadata(title="Song", artist="Artist")
        secondary = TrackMetadata(
            title="Other Song",
            artist="Other Artist",
            album="Album",
            duration_seconds=200.0,
            isrc="US1234567890",
        )
        merged = primary.merge(secondary)
        # Primary fields preserved
        assert merged.title == "Song"
        assert merged.artist == "Artist"
        # Filled from secondary
        assert merged.album == "Album"
        assert merged.duration_seconds == 200.0
        assert merged.isrc == "US1234567890"

    def test_merge_preserves_extra(self):
        """merge combines extra dicts with primary taking precedence."""
        primary = TrackMetadata(extra={"key1": "val1", "shared": "primary"})
        secondary = TrackMetadata(extra={"key2": "val2", "shared": "secondary"})
        merged = primary.merge(secondary)
        assert merged.extra["key1"] == "val1"
        assert merged.extra["key2"] == "val2"
        assert merged.extra["shared"] == "primary"


class TestSpotifyResolver:
    """Test the Spotify metadata resolver."""

    @pytest.mark.asyncio
    async def test_can_handle_spotify_track_url(self):
        """can_handle returns True for Spotify track URLs."""
        from app.resolvers.spotify import SpotifyResolver

        resolver = SpotifyResolver(client_id="id", client_secret="secret")
        assert resolver.can_handle("https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv") is True

    @pytest.mark.asyncio
    async def test_can_handle_spotify_intl_url(self):
        """can_handle returns True for Spotify international URLs."""
        from app.resolvers.spotify import SpotifyResolver

        resolver = SpotifyResolver(client_id="id", client_secret="secret")
        assert resolver.can_handle("https://open.spotify.com/intl-us/track/4u7EnebtmKWzUH433cf5Qv") is True

    @pytest.mark.asyncio
    async def test_can_handle_non_spotify(self):
        """can_handle returns False for non-Spotify URLs."""
        from app.resolvers.spotify import SpotifyResolver

        resolver = SpotifyResolver(client_id="id", client_secret="secret")
        assert resolver.can_handle("https://www.youtube.com/watch?v=abc") is False

    @pytest.mark.asyncio
    async def test_can_handle_spotify_uri(self):
        """can_handle returns True for Spotify URIs."""
        from app.resolvers.spotify import SpotifyResolver

        resolver = SpotifyResolver(client_id="id", client_secret="secret")
        assert resolver.can_handle("spotify:track:4u7EnebtmKWzUH433cf5Qv") is True

    @pytest.mark.asyncio
    async def test_resolve_extracts_metadata(self):
        """resolve() extracts title, artist, album, duration from Spotify API."""
        from app.resolvers.spotify import SpotifyResolver

        mock_track_response = {
            "name": "Bohemian Rhapsody",
            "artists": [{"name": "Queen"}],
            "album": {
                "name": "A Night at the Opera",
                "images": [{"url": "https://i.scdn.co/image/abc"}],
                "release_date": "1975-10-31",
            },
            "duration_ms": 354000,
            "external_ids": {"isrc": "GBUM71029604"},
            "uri": "spotify:track:4u7EnebtmKWzUH433cf5Qv",
            "explicit": False,
            "popularity": 85,
            "preview_url": "https://p.scdn.co/mp3-preview/abc",
        }

        resolver = SpotifyResolver(client_id="test-id", client_secret="test-secret")

        # Mock the token acquisition and API call
        with patch.object(resolver, "_ensure_token", return_value="mock-token"), \
             patch.object(resolver, "_get_session") as mock_get_session:

            mock_session = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_track_response)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_get_session.return_value = mock_session

            metadata = await resolver.resolve(
                "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
            )

        assert metadata.title == "Bohemian Rhapsody"
        assert metadata.artist == "Queen"
        assert metadata.album == "A Night at the Opera"
        assert metadata.duration_seconds == 354.0
        assert metadata.isrc == "GBUM71029604"
        assert metadata.provider == "spotify"
        assert metadata.provider_id == "4u7EnebtmKWzUH433cf5Qv"

    @pytest.mark.asyncio
    async def test_resolve_returns_partial_on_missing_credentials(self):
        """resolve() returns partial metadata when credentials are missing."""
        from app.resolvers.spotify import SpotifyResolver

        resolver = SpotifyResolver(client_id="", client_secret="")

        metadata = await resolver.resolve(
            "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
        )

        assert metadata.provider == "spotify"
        assert metadata.provider_id == "4u7EnebtmKWzUH433cf5Qv"
        assert metadata.title is None


class TestYouTubeResolver:
    """Test the YouTube metadata resolver."""

    @pytest.mark.asyncio
    async def test_can_handle_youtube_watch_url(self):
        """can_handle returns True for youtube.com/watch URLs."""
        from app.resolvers.youtube import YouTubeResolver

        resolver = YouTubeResolver()
        assert resolver.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True

    @pytest.mark.asyncio
    async def test_can_handle_youtu_be_url(self):
        """can_handle returns True for youtu.be short URLs."""
        from app.resolvers.youtube import YouTubeResolver

        resolver = YouTubeResolver()
        assert resolver.can_handle("https://youtu.be/dQw4w9WgXcQ") is True

    @pytest.mark.asyncio
    async def test_can_handle_youtube_music_url(self):
        """can_handle returns True for music.youtube.com URLs."""
        from app.resolvers.youtube import YouTubeResolver

        resolver = YouTubeResolver()
        assert resolver.can_handle("https://music.youtube.com/watch?v=abc") is True

    @pytest.mark.asyncio
    async def test_can_handle_non_youtube(self):
        """can_handle returns False for non-YouTube URLs."""
        from app.resolvers.youtube import YouTubeResolver

        resolver = YouTubeResolver()
        assert resolver.can_handle("https://open.spotify.com/track/abc") is False

    @pytest.mark.asyncio
    async def test_resolve_extracts_metadata_from_ytdlp(self):
        """resolve() extracts title, artist, duration from yt-dlp info."""
        from app.resolvers.youtube import YouTubeResolver

        mock_info = {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "track": "Never Gonna Give You Up",
            "artist": "Rick Astley",
            "album": "Whenever You Need Somebody",
            "duration": 213,
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "view_count": 1_500_000_000,
            "upload_date": "20091025",
        }

        resolver = YouTubeResolver()

        with patch.object(resolver, "_extract_info", return_value=mock_info):
            metadata = await resolver.resolve(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )

        assert metadata.title == "Never Gonna Give You Up"
        assert metadata.artist == "Rick Astley"
        assert metadata.album == "Whenever You Need Somebody"
        assert metadata.duration_seconds == 213.0
        assert metadata.provider == "youtube"
        assert metadata.provider_id == "dQw4w9WgXcQ"

    @pytest.mark.asyncio
    async def test_resolve_fallback_on_extraction_failure(self):
        """resolve() returns partial metadata when yt-dlp fails."""
        from app.resolvers.youtube import YouTubeResolver

        resolver = YouTubeResolver()

        with patch.object(resolver, "_extract_info", side_effect=Exception("Network error")):
            metadata = await resolver.resolve(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )

        assert metadata.provider == "youtube"
        assert metadata.provider_id == "dQw4w9WgXcQ"
        assert metadata.title is None

    @pytest.mark.asyncio
    async def test_resolve_uses_uploader_when_no_artist(self):
        """resolve() falls back to uploader when artist is not available."""
        from app.resolvers.youtube import YouTubeResolver

        mock_info = {
            "id": "abc123",
            "title": "Cool Song",
            "artist": None,
            "creator": None,
            "uploader": "Some Channel",
            "channel": "Some Channel",
            "duration": 180,
        }

        resolver = YouTubeResolver()

        with patch.object(resolver, "_extract_info", return_value=mock_info):
            metadata = await resolver.resolve("https://youtu.be/abc123")

        assert metadata.artist == "Some Channel"


class TestYouTubeVideoIdExtraction:
    """Test YouTube video ID extraction from various URL formats."""

    def test_extract_from_standard_url(self):
        """Extract ID from youtube.com/watch?v=ID."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_short_url(self):
        """Extract ID from youtu.be/ID."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_embed_url(self):
        """Extract ID from youtube.com/embed/ID."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_shorts_url(self):
        """Extract ID from youtube.com/shorts/ID."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_from_music_youtube(self):
        """Extract ID from music.youtube.com/watch?v=ID."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://music.youtube.com/watch?v=abc123") == "abc123"

    def test_extract_returns_none_for_invalid(self):
        """Returns None for non-YouTube URLs."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id("https://open.spotify.com/track/abc") is None

    def test_extract_with_extra_params(self):
        """Extract ID when URL has additional query parameters."""
        from app.resolvers.youtube import _extract_video_id

        assert _extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        ) == "dQw4w9WgXcQ"


class TestSpotifyTrackIdExtraction:
    """Test Spotify track ID extraction."""

    def test_extract_from_standard_url(self):
        """Extract ID from standard Spotify track URL."""
        from app.resolvers.spotify import _extract_track_id

        assert _extract_track_id("https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv") == "4u7EnebtmKWzUH433cf5Qv"

    def test_extract_from_intl_url(self):
        """Extract ID from international Spotify URL."""
        from app.resolvers.spotify import _extract_track_id

        assert _extract_track_id("https://open.spotify.com/intl-us/track/4u7EnebtmKWzUH433cf5Qv") == "4u7EnebtmKWzUH433cf5Qv"

    def test_extract_from_uri(self):
        """Extract ID from Spotify URI."""
        from app.resolvers.spotify import _extract_track_id

        assert _extract_track_id("spotify:track:4u7EnebtmKWzUH433cf5Qv") == "4u7EnebtmKWzUH433cf5Qv"

    def test_returns_none_for_non_track(self):
        """Returns None for non-track Spotify URLs."""
        from app.resolvers.spotify import _extract_track_id

        assert _extract_track_id("https://open.spotify.com/album/abc123") is None

    def test_returns_none_for_invalid_url(self):
        """Returns None for non-Spotify URLs."""
        from app.resolvers.spotify import _extract_track_id

        assert _extract_track_id("https://www.youtube.com/watch?v=abc") is None
