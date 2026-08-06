"""Application configuration using pydantic-settings with .env file support."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Slaptastic application settings.

    All values can be overridden via environment variables or a .env file.
    Environment variables are prefixed with nothing (flat namespace) and are
    case-insensitive.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Mattermost ---
    mattermost_url: str = Field(
        default="http://localhost:8065",
        description="Mattermost server URL",
    )
    mattermost_token: str = Field(
        default="",
        description="Mattermost bot personal access token",
    )
    mattermost_channel: str = Field(
        default="",
        description="Default Mattermost channel ID to post in",
    )
    bot_username: str = Field(
        default="slaptastic",
        description="Bot username in Mattermost",
    )
    bot_mention_trigger: str = Field(
        default="@slaptastic",
        description="Mention trigger that activates the bot",
    )

    # --- Jellyfin ---
    jellyfin_url: str = Field(
        default="http://localhost:8096",
        description="Jellyfin server URL",
    )
    jellyfin_token: str = Field(
        default="",
        description="Jellyfin API token",
    )

    # --- Music Library ---
    music_base_path: Path = Field(
        default=Path("/app/music"),
        description="Base filesystem path where music files are stored",
    )
    download_enabled: bool = Field(
        default=False,
        description="Whether downloading new music is enabled",
    )
    mp3_bitrate: int = Field(
        default=320,
        description="Target MP3 bitrate in kbps for transcoding",
    )

    # --- Matching Thresholds ---
    auto_approve_threshold: float = Field(
        default=0.55,
        description="Confidence threshold above which matches are auto-approved",
    )
    manual_review_threshold: float = Field(
        default=0.40,
        description="Confidence threshold below which matches require manual review",
    )

    # --- Database ---
    db_url: str = Field(
        default="sqlite+aiosqlite:////app/data/slaptastic.db",
        description="Async SQLAlchemy database URL",
    )

    # --- Security ---
    admin_api_token: str = Field(
        default="",
        description="Admin API bearer token for protected endpoints",
    )

    # --- yt-dlp Options ---
    ytdlp_format: str = Field(
        default="bestaudio/best",
        description="yt-dlp format selection string",
    )
    ytdlp_extract_audio: bool = Field(
        default=True,
        description="Whether yt-dlp should extract audio from video",
    )
    ytdlp_audio_format: str = Field(
        default="mp3",
        description="Target audio format for yt-dlp extraction",
    )
    ytdlp_audio_quality: str = Field(
        default="0",
        description="yt-dlp audio quality (0 = best)",
    )
    ytdlp_embed_metadata: bool = Field(
        default=True,
        description="Whether to embed metadata in downloaded files",
    )
    ytdlp_embed_thumbnail: bool = Field(
        default=True,
        description="Whether to embed thumbnail art in downloaded files",
    )
    ytdlp_concurrent_fragments: int = Field(
        default=4,
        description="Number of concurrent download fragments",
    )
    ytdlp_rate_limit: str | None = Field(
        default=None,
        description="Download rate limit (e.g. '5M' for 5MB/s)",
    )

    # --- Spotify ---
    spotify_client_id: str = Field(
        default="",
        description="Spotify API client ID for metadata lookups",
    )
    spotify_client_secret: str = Field(
        default="",
        description="Spotify API client secret",
    )

    # --- Apple Music ---
    apple_music_token: str = Field(
        default="",
        description="Apple Music API developer token (JWT)",
    )

    # --- Scrobble-based auto-add (multi-scrobbler + Maloja) ---
    scrobbler_enabled: bool = Field(
        default=False,
        description="Enable polling multi-scrobbler for friends' plays to auto-add music",
    )
    multiscrobbler_url: str = Field(
        default="http://multi-scrobbler:9078",
        description="Base URL of the multi-scrobbler instance (per-friend play data)",
    )
    maloja_url: str = Field(
        default="http://maloja:42010",
        description="Base URL of the Maloja scrobble store (durable play counts + stats)",
    )
    maloja_api_key: str = Field(
        default="",
        description="Maloja API key",
    )
    scrobble_add_threshold: int = Field(
        default=3,
        description="Add a track after a friend has played it at least this many times",
    )
    scrobble_lookback_days: int = Field(
        default=7,
        description="Only consider plays within this many days when auto-adding",
    )
    scrobble_poll_interval_seconds: int = Field(
        default=3600,
        description="How often to poll multi-scrobbler/Maloja for new qualifying plays",
    )
    scrobble_announce_in_mattermost: bool = Field(
        default=True,
        description="Announce scrobble-based auto-adds in the Mattermost channel",
    )

    @property
    def ytdlp_opts(self) -> dict:
        """Build a yt-dlp options dictionary from settings."""
        opts: dict = {
            "format": self.ytdlp_format,
            "extract_audio": self.ytdlp_extract_audio,
            "audio_format": self.ytdlp_audio_format,
            "audio_quality": self.ytdlp_audio_quality,
            "embed_metadata": self.ytdlp_embed_metadata,
            "embed_thumbnail": self.ytdlp_embed_thumbnail,
            "concurrent_fragment_downloads": self.ytdlp_concurrent_fragments,
            "outtmpl": "%(artist)s - %(title)s.%(ext)s",
            "quiet": True,
            "no_warnings": True,
        }
        if self.ytdlp_rate_limit:
            opts["ratelimit"] = self.ytdlp_rate_limit
        return opts


def get_settings() -> Settings:
    """Create and return an application Settings instance.

    This is a factory function rather than a cached singleton so tests can
    easily override environment variables and get fresh settings.
    """
    return Settings()
