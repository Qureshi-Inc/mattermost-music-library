"""Tests for Mattermost integration - URL detection, command parsing, formatting."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mattermost.client import (
    COMMAND_PATTERN,
    MUSIC_URL_COMBINED,
    MUSIC_URL_PATTERNS,
    IncomingMessage,
    MattermostClient,
    MattermostConfig,
)
from app.mattermost.commands import (
    VALID_COMMANDS,
    CommandHandler,
    ParsedCommand,
    parse_command,
)


class TestMusicURLDetection:
    """Test regex patterns for detecting music URLs in messages."""

    def test_detect_youtube_watch_url(self):
        """Detects standard YouTube watch URLs."""
        text = "Check this out: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 1
        assert "youtube.com/watch" in matches[0]
        assert "dQw4w9WgXcQ" in matches[0]

    def test_detect_youtube_short_url(self):
        """Detects youtu.be short URLs."""
        text = "Listen: https://youtu.be/dQw4w9WgXcQ"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 1
        assert "youtu.be" in matches[0]

    def test_detect_spotify_track_url(self):
        """Detects Spotify track URLs."""
        text = "New song: https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 1
        assert "spotify.com/track" in matches[0]

    def test_detect_apple_music_url(self):
        """Detects Apple Music URLs."""
        text = "Found this: https://music.apple.com/us/album/bohemian-rhapsody/1440806041?i=1440806768"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 1
        assert "music.apple.com" in matches[0]

    def test_no_match_for_random_urls(self):
        """Does not match non-music URLs."""
        text = "Check out https://www.google.com and https://github.com/foo"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 0

    def test_multiple_urls_in_one_message(self):
        """Detects multiple music URLs in a single message."""
        text = (
            "Compare these: https://www.youtube.com/watch?v=abc123 "
            "and https://open.spotify.com/track/def456"
        )
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 2

    def test_youtube_url_with_extra_params(self):
        """Detects YouTube URL with list and time parameters."""
        text = "https://www.youtube.com/watch?v=abc123&list=PLfoo&t=42"
        matches = MUSIC_URL_COMBINED.findall(text)
        assert len(matches) == 1

    def test_individual_patterns_youtube(self):
        """Individual YouTube patterns match correctly."""
        urls = [
            "https://www.youtube.com/watch?v=abc123",
            "https://youtu.be/abc123",
        ]
        for url in urls:
            matched = any(p.search(url) for p in MUSIC_URL_PATTERNS)
            assert matched, f"Pattern should match: {url}"

    def test_individual_patterns_spotify(self):
        """Individual Spotify pattern matches correctly."""
        url = "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
        matched = any(p.search(url) for p in MUSIC_URL_PATTERNS)
        assert matched

    def test_individual_patterns_apple_music(self):
        """Individual Apple Music pattern matches correctly."""
        url = "https://music.apple.com/us/album/song/123456?i=789"
        matched = any(p.search(url) for p in MUSIC_URL_PATTERNS)
        assert matched


class TestCommandParsing:
    """Test @slaptastic command parsing."""

    def test_command_pattern_matches_status(self):
        """Regex matches @slaptastic status."""
        text = "@slaptastic status"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "status"
        assert match.group(2) is None

    def test_command_pattern_matches_add_with_number(self):
        """Regex matches @slaptastic add 1."""
        text = "@slaptastic add 1"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "add"
        assert match.group(2).strip() == "1"

    def test_command_pattern_matches_approve(self):
        """Regex matches @slaptastic approve."""
        text = "@slaptastic approve"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "approve"

    def test_command_pattern_matches_cancel(self):
        """Regex matches @slaptastic cancel."""
        text = "@slaptastic cancel"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "cancel"

    def test_command_pattern_matches_retry(self):
        """Regex matches @slaptastic retry."""
        text = "@slaptastic retry"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "retry"

    def test_command_pattern_matches_candidates(self):
        """Regex matches @slaptastic candidates."""
        text = "@slaptastic candidates"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "candidates"

    def test_command_pattern_case_insensitive(self):
        """Command matching is case-insensitive."""
        text = "@Slaptastic STATUS"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "STATUS"

    def test_command_pattern_in_longer_message(self):
        """Command is detected even within a longer message."""
        text = "Hey everyone, @slaptastic status please"
        match = COMMAND_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == "status"

    def test_command_pattern_no_match_without_command(self):
        """No match when just @slaptastic without a command."""
        text = "@slaptastic"
        match = COMMAND_PATTERN.search(text)
        # Pattern requires at least one word after @slaptastic
        assert match is None


class TestParseCommand:
    """Test the parse_command function."""

    def test_parse_valid_command(self):
        """parse_command returns ParsedCommand for valid commands."""
        result = parse_command("status", None, "@slaptastic status")
        assert result is not None
        assert result.name == "status"
        assert result.args is None

    def test_parse_command_with_args(self):
        """parse_command preserves arguments."""
        result = parse_command("add", "3", "@slaptastic add 3")
        assert result is not None
        assert result.name == "add"
        assert result.args == "3"

    def test_parse_invalid_command_returns_none(self):
        """parse_command returns None for unrecognized commands."""
        result = parse_command("explode", None, "@slaptastic explode")
        assert result is None

    def test_parse_none_command_returns_none(self):
        """parse_command returns None when command_name is None."""
        result = parse_command(None, None, "")
        assert result is None

    def test_parse_empty_command_returns_none(self):
        """parse_command returns None when command_name is empty."""
        result = parse_command("", None, "")
        assert result is None

    def test_all_valid_commands_accepted(self):
        """All commands in VALID_COMMANDS are accepted by parse_command."""
        for cmd in VALID_COMMANDS:
            result = parse_command(cmd, None, f"@slaptastic {cmd}")
            assert result is not None, f"Command '{cmd}' should be valid"
            assert result.name == cmd


class TestCommandHandler:
    """Test the CommandHandler dispatch logic."""

    @pytest.fixture
    def mock_job_service(self):
        """Create a mock job service implementing the protocol."""
        service = AsyncMock()
        service.get_status = AsyncMock(return_value={
            "state": "pending",
            "track_title": "Test Song",
            "artist": "Test Artist",
        })
        service.get_candidates = AsyncMock(return_value=[
            {"number": 1, "title": "Song 1", "artist": "Artist 1", "score": 0.9, "duration": 200},
            {"number": 2, "title": "Song 2", "artist": "Artist 2", "score": 0.8, "duration": 180},
        ])
        service.select_candidate = AsyncMock(return_value={
            "title": "Song 1",
            "artist": "Artist 1",
            "album": "Album",
            "state": "approved",
        })
        service.approve = AsyncMock(return_value={
            "title": "Song 1",
            "artist": "Artist 1",
            "state": "downloading",
        })
        service.cancel = AsyncMock(return_value={
            "title": "Song 1",
            "state": "cancelled",
        })
        service.retry = AsyncMock(return_value={
            "title": "Song 1",
            "artist": "Artist 1",
            "state": "pending",
        })
        return service

    @pytest.mark.asyncio
    async def test_handle_status_calls_job_service(self, mock_job_service):
        """handle dispatches 'status' to job_service.get_status."""
        # We need a MessageFormatter, which may not exist yet, so we mock it
        with patch("app.mattermost.commands.MessageFormatter") as mock_formatter_cls:
            mock_fmt = MagicMock()
            mock_fmt.format_status = MagicMock(return_value="Status: pending")
            mock_formatter_cls.return_value = mock_fmt

            handler = CommandHandler(mock_job_service)
            command = ParsedCommand(name="status", args=None, raw="@slaptastic status")
            await handler.handle(command, "channel-1", "user-1")

            mock_job_service.get_status.assert_called_once_with("channel-1", "user-1")

    @pytest.mark.asyncio
    async def test_handle_add_with_valid_number(self, mock_job_service):
        """handle dispatches 'add 1' to job_service.select_candidate."""
        with patch("app.mattermost.commands.MessageFormatter") as mock_formatter_cls:
            mock_fmt = MagicMock()
            mock_fmt.format_candidate_selected = MagicMock(return_value="Selected #1")
            mock_formatter_cls.return_value = mock_fmt

            handler = CommandHandler(mock_job_service)
            command = ParsedCommand(name="add", args="1", raw="@slaptastic add 1")
            await handler.handle(command, "channel-1", "user-1")

            mock_job_service.select_candidate.assert_called_once_with("channel-1", "user-1", 1)

    @pytest.mark.asyncio
    async def test_handle_add_with_invalid_number(self, mock_job_service):
        """handle returns error for 'add abc' (non-numeric)."""
        with patch("app.mattermost.commands.MessageFormatter") as mock_formatter_cls:
            mock_fmt = MagicMock()
            mock_fmt.format_error = MagicMock(return_value="Error: invalid number")
            mock_formatter_cls.return_value = mock_fmt

            handler = CommandHandler(mock_job_service)
            command = ParsedCommand(name="add", args="abc", raw="@slaptastic add abc")
            await handler.handle(command, "channel-1", "user-1")

            mock_fmt.format_error.assert_called_once()
            mock_job_service.select_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_add_without_args(self, mock_job_service):
        """handle returns error for 'add' without a number."""
        with patch("app.mattermost.commands.MessageFormatter") as mock_formatter_cls:
            mock_fmt = MagicMock()
            mock_fmt.format_error = MagicMock(return_value="Error: specify number")
            mock_formatter_cls.return_value = mock_fmt

            handler = CommandHandler(mock_job_service)
            command = ParsedCommand(name="add", args=None, raw="@slaptastic add")
            await handler.handle(command, "channel-1", "user-1")

            mock_fmt.format_error.assert_called_once()


class TestMattermostConfig:
    """Test MattermostConfig dataclass."""

    def test_config_creation(self):
        """MattermostConfig can be created with required fields."""
        config = MattermostConfig(
            url="http://localhost:8065",
            bot_token="test-token",
            channel_id="test-channel",
        )
        assert config.url == "http://localhost:8065"
        assert config.bot_token == "test-token"
        assert config.channel_id == "test-channel"
        assert config.bot_username == "slaptastic"

    def test_config_defaults(self):
        """MattermostConfig has sensible defaults."""
        config = MattermostConfig(
            url="http://localhost:8065",
            bot_token="token",
            channel_id="channel",
        )
        assert config.reconnect_base_delay == 1.0
        assert config.reconnect_max_delay == 60.0
        assert config.reconnect_max_attempts == 0


class TestMattermostClientURLs:
    """Test MattermostClient URL construction."""

    def test_api_url_construction(self):
        """api_url appends /api/v4 to base URL."""
        config = MattermostConfig(
            url="http://mattermost.example.com",
            bot_token="token",
            channel_id="channel",
        )
        client = MattermostClient(config)
        assert client.api_url == "http://mattermost.example.com/api/v4"

    def test_api_url_strips_trailing_slash(self):
        """api_url handles trailing slash in base URL."""
        config = MattermostConfig(
            url="http://mattermost.example.com/",
            bot_token="token",
            channel_id="channel",
        )
        client = MattermostClient(config)
        assert client.api_url == "http://mattermost.example.com/api/v4"

    def test_ws_url_http_to_ws(self):
        """ws_url converts http to ws scheme."""
        config = MattermostConfig(
            url="http://mattermost.example.com",
            bot_token="token",
            channel_id="channel",
        )
        client = MattermostClient(config)
        assert client.ws_url.startswith("ws://")
        assert "/api/v4/websocket" in client.ws_url

    def test_ws_url_https_to_wss(self):
        """ws_url converts https to wss scheme."""
        config = MattermostConfig(
            url="https://mattermost.example.com",
            bot_token="token",
            channel_id="channel",
        )
        client = MattermostClient(config)
        assert client.ws_url.startswith("wss://")


class TestIncomingMessage:
    """Test IncomingMessage dataclass."""

    def test_message_with_music_url(self):
        """IncomingMessage can hold detected music URLs."""
        msg = IncomingMessage(
            post_id="post1",
            channel_id="chan1",
            user_id="user1",
            username="john",
            message="Check this: https://youtu.be/abc123",
            root_id="",
            music_urls=["https://youtu.be/abc123"],
        )
        assert len(msg.music_urls) == 1

    def test_message_with_command(self):
        """IncomingMessage can hold a parsed command."""
        msg = IncomingMessage(
            post_id="post1",
            channel_id="chan1",
            user_id="user1",
            username="john",
            message="@slaptastic status",
            root_id="",
            command="status",
            command_args=None,
        )
        assert msg.command == "status"

    def test_message_defaults(self):
        """IncomingMessage has sensible defaults for optional fields."""
        msg = IncomingMessage(
            post_id="post1",
            channel_id="chan1",
            user_id="user1",
            username="john",
            message="hello",
            root_id="",
        )
        assert msg.music_urls == []
        assert msg.command is None
        assert msg.command_args is None
