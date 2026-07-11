"""Tests for app.config module - settings loading, defaults, and validation."""

from pathlib import Path


class TestSettingsDefaults:
    """Test that Settings has correct default values when no env vars are set."""

    def test_default_mattermost_url(self, monkeypatch):
        """Default Mattermost URL is localhost:8065."""
        monkeypatch.delenv("MATTERMOST_URL", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.mattermost_url == "http://localhost:8065"

    def test_default_bot_username(self, monkeypatch):
        """Default bot username is 'slaptastic'."""
        monkeypatch.delenv("BOT_USERNAME", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.bot_username == "slaptastic"

    def test_default_music_base_path(self, monkeypatch):
        """Default music base path is /music."""
        monkeypatch.delenv("MUSIC_BASE_PATH", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.music_base_path == Path("/music")

    def test_default_mp3_bitrate(self, monkeypatch):
        """Default MP3 bitrate is 320."""
        monkeypatch.delenv("MP3_BITRATE", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.mp3_bitrate == 320

    def test_default_auto_approve_threshold(self, monkeypatch):
        """Default auto-approve threshold is 0.90."""
        monkeypatch.delenv("AUTO_APPROVE_THRESHOLD", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.auto_approve_threshold == 0.90

    def test_default_manual_review_threshold(self, monkeypatch):
        """Default manual-review threshold is 0.70."""
        monkeypatch.delenv("MANUAL_REVIEW_THRESHOLD", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.manual_review_threshold == 0.70

    def test_default_db_url(self, monkeypatch):
        """Default database URL is sqlite file."""
        monkeypatch.delenv("DB_URL", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert "sqlite" in s.db_url

    def test_default_download_disabled(self, monkeypatch):
        """Downloading is disabled by default."""
        monkeypatch.delenv("DOWNLOAD_ENABLED", raising=False)
        from app.config import Settings

        s = Settings(mattermost_token="x", admin_api_token="x")
        assert s.download_enabled is False


class TestSettingsFromEnv:
    """Test that environment variables override defaults."""

    def test_env_overrides_mattermost_url(self, test_settings):
        """MATTERMOST_URL env var overrides the default."""
        assert test_settings.mattermost_url == "http://localhost:8065"

    def test_env_overrides_admin_token(self, test_settings):
        """ADMIN_API_TOKEN env var is loaded."""
        assert test_settings.admin_api_token == "test-admin-token"

    def test_env_overrides_music_base_path(self, test_settings):
        """MUSIC_BASE_PATH env var overrides the default."""
        assert test_settings.music_base_path == Path("/tmp/test-music")

    def test_env_overrides_db_url(self, test_settings):
        """DB_URL env var overrides the default."""
        assert test_settings.db_url == "sqlite+aiosqlite:///:memory:"


class TestSettingsYtdlpOpts:
    """Test the ytdlp_opts property."""

    def test_ytdlp_opts_contains_format(self, test_settings):
        """ytdlp_opts dict includes the format selection."""
        opts = test_settings.ytdlp_opts
        assert opts["format"] == "bestaudio/best"

    def test_ytdlp_opts_extract_audio(self, test_settings):
        """ytdlp_opts enables audio extraction."""
        opts = test_settings.ytdlp_opts
        assert opts["extract_audio"] is True

    def test_ytdlp_opts_audio_format(self, test_settings):
        """ytdlp_opts targets mp3."""
        opts = test_settings.ytdlp_opts
        assert opts["audio_format"] == "mp3"

    def test_ytdlp_opts_no_ratelimit_when_none(self, test_settings):
        """ytdlp_opts does not include ratelimit when not set."""
        test_settings.ytdlp_rate_limit = None
        opts = test_settings.ytdlp_opts
        assert "ratelimit" not in opts

    def test_ytdlp_opts_includes_ratelimit_when_set(self, test_settings):
        """ytdlp_opts includes ratelimit when configured."""
        test_settings.ytdlp_rate_limit = "5M"
        opts = test_settings.ytdlp_opts
        assert opts["ratelimit"] == "5M"

    def test_ytdlp_opts_quiet_mode(self, test_settings):
        """ytdlp_opts suppresses output."""
        opts = test_settings.ytdlp_opts
        assert opts["quiet"] is True
        assert opts["no_warnings"] is True

    def test_ytdlp_opts_concurrent_fragments(self, test_settings):
        """ytdlp_opts includes concurrent fragment count."""
        opts = test_settings.ytdlp_opts
        assert opts["concurrent_fragment_downloads"] == 4


class TestGetSettings:
    """Test the get_settings factory function."""

    def test_get_settings_returns_settings_instance(self):
        """get_settings() returns a Settings instance."""
        from app.config import Settings, get_settings

        s = get_settings()
        assert isinstance(s, Settings)

    def test_get_settings_is_not_cached(self):
        """Each call to get_settings() returns a new instance."""
        from app.config import get_settings

        s1 = get_settings()
        s2 = get_settings()
        # They should be equal but not the same object
        assert s1 is not s2

    def test_get_settings_reads_env(self, monkeypatch):
        """get_settings picks up environment variable changes."""
        monkeypatch.setenv("ADMIN_API_TOKEN", "new-token-value")
        from app.config import get_settings

        s = get_settings()
        assert s.admin_api_token == "new-token-value"
