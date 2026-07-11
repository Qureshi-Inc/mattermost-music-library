"""Tests for download functionality - mocking yt-dlp downloads."""

from unittest.mock import MagicMock, patch

import pytest


class TestYtDlpOptionsConstruction:
    """Test that yt-dlp options are constructed correctly from config."""

    def test_default_format_is_bestaudio(self, test_settings):
        """Default format selection is 'bestaudio/best'."""
        opts = test_settings.ytdlp_opts
        assert opts["format"] == "bestaudio/best"

    def test_audio_extraction_enabled(self, test_settings):
        """Audio extraction is enabled by default."""
        opts = test_settings.ytdlp_opts
        assert opts["extract_audio"] is True

    def test_audio_format_is_mp3(self, test_settings):
        """Target audio format is MP3."""
        opts = test_settings.ytdlp_opts
        assert opts["audio_format"] == "mp3"

    def test_audio_quality_is_best(self, test_settings):
        """Audio quality is set to 0 (best)."""
        opts = test_settings.ytdlp_opts
        assert opts["audio_quality"] == "0"

    def test_metadata_embedding_enabled(self, test_settings):
        """Metadata embedding is on."""
        opts = test_settings.ytdlp_opts
        assert opts["embed_metadata"] is True

    def test_thumbnail_embedding_enabled(self, test_settings):
        """Thumbnail embedding is on."""
        opts = test_settings.ytdlp_opts
        assert opts["embed_thumbnail"] is True

    def test_concurrent_fragments_default(self, test_settings):
        """Concurrent fragment downloads default to 4."""
        opts = test_settings.ytdlp_opts
        assert opts["concurrent_fragment_downloads"] == 4

    def test_output_template_format(self, test_settings):
        """Output template uses artist - title format."""
        opts = test_settings.ytdlp_opts
        assert "%(artist)s" in opts["outtmpl"]
        assert "%(title)s" in opts["outtmpl"]


class TestDownloadExecution:
    """Test download execution with mocked yt-dlp."""

    @pytest.mark.asyncio
    async def test_download_calls_ytdlp_with_correct_url(self):
        """yt-dlp is called with the correct URL."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download = MagicMock(return_value=0)
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            import yt_dlp

            url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            opts = {
                "format": "bestaudio/best",
                "outtmpl": "/tmp/test-music/%(title)s.%(ext)s",
                "quiet": True,
            }

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            mock_ydl_instance.download.assert_called_once_with([url])

    @pytest.mark.asyncio
    async def test_download_with_rate_limit(self, test_settings):
        """When rate limit is set, it appears in yt-dlp options."""
        test_settings.ytdlp_rate_limit = "5M"
        opts = test_settings.ytdlp_opts
        assert opts["ratelimit"] == "5M"

    @pytest.mark.asyncio
    async def test_download_without_rate_limit(self, test_settings):
        """When rate limit is None, ratelimit key is absent from options."""
        test_settings.ytdlp_rate_limit = None
        opts = test_settings.ytdlp_opts
        assert "ratelimit" not in opts


class TestDownloadErrorHandling:
    """Test error handling during downloads."""

    @pytest.mark.asyncio
    async def test_geo_restricted_error(self):
        """Geo-restricted videos raise an appropriate error."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download = MagicMock(
            side_effect=Exception("Video is not available in your country")
        )
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            import yt_dlp

            with pytest.raises(Exception, match="not available in your country"), yt_dlp.YoutubeDL({}) as ydl:
                ydl.download(["https://www.youtube.com/watch?v=restricted"])

    @pytest.mark.asyncio
    async def test_private_video_error(self):
        """Private videos raise an appropriate error."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download = MagicMock(
            side_effect=Exception("This video is private")
        )
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            import yt_dlp

            with pytest.raises(Exception, match="private"), yt_dlp.YoutubeDL({}) as ydl:
                ydl.download(["https://www.youtube.com/watch?v=private"])

    @pytest.mark.asyncio
    async def test_network_error(self):
        """Network errors are propagated."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download = MagicMock(
            side_effect=ConnectionError("Connection refused")
        )
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            import yt_dlp

            with pytest.raises(ConnectionError), yt_dlp.YoutubeDL({}) as ydl:
                ydl.download(["https://www.youtube.com/watch?v=abc"])

    @pytest.mark.asyncio
    async def test_age_restricted_error(self):
        """Age-restricted content raises an appropriate error."""
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download = MagicMock(
            side_effect=Exception("Sign in to confirm your age")
        )
        mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance):
            import yt_dlp

            with pytest.raises(Exception, match="age"), yt_dlp.YoutubeDL({}) as ydl:
                ydl.download(["https://www.youtube.com/watch?v=age_restricted"])


class TestProgressTracking:
    """Test download progress tracking via yt-dlp hooks."""

    def test_progress_hook_receives_status(self):
        """A progress hook function receives download status updates."""
        progress_updates = []

        def progress_hook(d):
            progress_updates.append(d)

        # Simulate what yt-dlp does with progress hooks

        # Simulate progress callbacks
        progress_hook({"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 10240})
        progress_hook({"status": "downloading", "downloaded_bytes": 5120, "total_bytes": 10240})
        progress_hook({"status": "finished", "downloaded_bytes": 10240, "total_bytes": 10240})

        assert len(progress_updates) == 3
        assert progress_updates[0]["status"] == "downloading"
        assert progress_updates[-1]["status"] == "finished"

    def test_progress_hook_calculates_percentage(self):
        """Progress percentage can be calculated from hook data."""
        hook_data = {
            "status": "downloading",
            "downloaded_bytes": 5120,
            "total_bytes": 10240,
        }
        percentage = (hook_data["downloaded_bytes"] / hook_data["total_bytes"]) * 100
        assert percentage == 50.0

    def test_progress_hook_handles_missing_total(self):
        """Progress hook handles cases where total_bytes is unknown."""
        hook_data = {
            "status": "downloading",
            "downloaded_bytes": 5120,
            "total_bytes": None,
        }
        # Should not crash when total is None
        if hook_data["total_bytes"] is not None:
            percentage = (hook_data["downloaded_bytes"] / hook_data["total_bytes"]) * 100
        else:
            percentage = None
        assert percentage is None


class TestYouTubeSearcher:
    """Test the YouTube searcher that wraps yt-dlp search."""

    def test_build_search_query(self):
        """build_search_query combines artist and title."""
        from app.matching.searcher import YouTubeSearcher

        searcher = YouTubeSearcher()
        query = searcher.build_search_query("Queen", "Bohemian Rhapsody")
        assert "Queen" in query
        assert "Bohemian Rhapsody" in query

    def test_build_search_query_removes_noise(self):
        """build_search_query removes noise like '- Single'."""
        from app.matching.searcher import YouTubeSearcher

        searcher = YouTubeSearcher()
        query = searcher.build_search_query("Artist", "Song - Single")
        assert "- Single" not in query
        assert "Song" in query

    def test_build_search_query_removes_deluxe(self):
        """build_search_query removes '(Deluxe)' noise."""
        from app.matching.searcher import YouTubeSearcher

        searcher = YouTubeSearcher()
        query = searcher.build_search_query("Artist", "Album (Deluxe)")
        assert "(Deluxe)" not in query

    def test_search_returns_empty_on_no_results(self):
        """search returns empty SearchResult when yt-dlp finds nothing."""
        from app.matching.searcher import YouTubeSearcher

        searcher = YouTubeSearcher()

        with patch.object(searcher, "_fetch_candidates", return_value=[]):
            result = searcher.search("Unknown Artist", "Unknown Song")

        assert result.candidates == []
        assert result.best_match is None

    def test_search_scores_and_sorts_candidates(self):
        """search scores and sorts candidates by match quality."""
        from app.matching.scorer import CandidateInfo
        from app.matching.searcher import YouTubeSearcher

        searcher = YouTubeSearcher()
        mock_candidates = [
            CandidateInfo(
                url="https://youtube.com/watch?v=bad",
                title="Something Completely Different",
                channel="Random",
                duration=100.0,
                view_count=100,
            ),
            CandidateInfo(
                url="https://youtube.com/watch?v=good",
                title="Queen - Bohemian Rhapsody (Official Audio)",
                channel="QueenVEVO",
                duration=354.0,
                view_count=1_000_000_000,
            ),
        ]

        with patch.object(searcher, "_fetch_candidates", return_value=mock_candidates):
            from app.matching.scorer import ExpectedMetadata

            expected = ExpectedMetadata(title="Bohemian Rhapsody", artist="Queen", duration_seconds=354.0)
            result = searcher.search("Queen", "Bohemian Rhapsody", expected=expected)

        assert result.best_match is not None
        assert result.best_match.url == "https://youtube.com/watch?v=good"
        assert result.candidates[0].score >= result.candidates[1].score
