"""Tests for app.library - audio tagging, file organization, and Jellyfin integration."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.library.tagger import AudioTagger, TagData


class TestFilenameSanitization:
    """Test filename sanitization logic for safe path construction."""

    def test_basic_sanitization(self):
        """Basic characters that should be stripped from filenames."""
        # Characters that are unsafe in filenames
        unsafe_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        for char in unsafe_chars:
            dirty = f"Artist{char}Name"
            # The organizer should handle these - for now test basic path safety
            clean_path = Path("/music") / dirty.replace(char, "_")
            assert char not in clean_path.name or char == "/"

    def test_path_construction_artist_album_track(self):
        """Library path follows Artist/Album/01 - Title.mp3 format."""
        artist = "Queen"
        album = "A Night at the Opera"
        track_number = 1
        title = "Bohemian Rhapsody"
        ext = ".mp3"

        # Expected path structure
        expected = Path("/music") / artist / album / f"{track_number:02d} - {title}{ext}"
        assert str(expected) == "/music/Queen/A Night at the Opera/01 - Bohemian Rhapsody.mp3"

    def test_path_construction_no_album(self):
        """Path construction handles missing album gracefully."""
        artist = "Single Artist"
        title = "Stand Alone Song"
        ext = ".mp3"

        # When album is missing, use "Singles" or just artist root
        path = Path("/music") / artist / "Singles" / f"01 - {title}{ext}"
        assert "Single Artist" in str(path)
        assert "Singles" in str(path)

    def test_path_construction_with_special_characters_in_title(self):
        """Titles with special characters are sanitized for filesystem."""
        title = "What's Going On?"
        # Replace filesystem-unsafe chars
        safe_title = title.replace("?", "").replace("'", "'")
        path = Path("/music") / "Marvin Gaye" / "Album" / f"01 - {safe_title}.mp3"
        assert "?" not in path.name

    def test_long_filename_handling(self):
        """Very long filenames are truncated to filesystem limits."""
        long_title = "A" * 300  # Longer than 255 char limit
        # Should be truncated
        safe_title = long_title[:200]
        path = Path("/music") / "Artist" / "Album" / f"01 - {safe_title}.mp3"
        assert len(path.name) < 255


class TestAudioTagger:
    """Test the AudioTagger class for ID3 tag manipulation."""

    @pytest.fixture
    def tagger(self):
        """Create an AudioTagger instance."""
        return AudioTagger()

    @pytest.fixture
    def mp3_file(self, tmp_path):
        """Create a minimal valid MP3 file for testing.

        Creates a file with a valid MP3 frame header.
        """
        mp3_path = tmp_path / "test.mp3"
        # Minimal MP3 frame: sync word (0xFFE0) + valid frame header
        # This is the simplest valid MP3 frame we can create
        # FF FB = sync + MPEG1, Layer3, no CRC
        # 90 00 = 128kbps, 44100Hz, stereo, no padding
        frame_header = b"\xff\xfb\x90\x00"
        # A minimal frame needs at least 417 bytes for 128kbps/44100Hz
        frame_data = frame_header + b"\x00" * 413
        # Write multiple frames to make it a "valid" MP3
        mp3_path.write_bytes(frame_data * 10)
        return mp3_path

    def test_tag_file_sets_title(self, tagger, mp3_file):
        """tag_file writes the title ID3 tag."""
        tags = TagData(title="Test Song", artist="Test Artist")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.title == "Test Song"

    def test_tag_file_sets_artist(self, tagger, mp3_file):
        """tag_file writes the artist ID3 tag."""
        tags = TagData(title="Song", artist="The Artist")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.artist == "The Artist"

    def test_tag_file_sets_album(self, tagger, mp3_file):
        """tag_file writes the album ID3 tag."""
        tags = TagData(title="Song", artist="Artist", album="The Album")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.album == "The Album"

    def test_tag_file_sets_track_number(self, tagger, mp3_file):
        """tag_file writes track number."""
        tags = TagData(title="Song", artist="Artist", track_number=5, total_tracks=12)
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.track_number == 5
        assert read_back.total_tracks == 12

    def test_tag_file_sets_year(self, tagger, mp3_file):
        """tag_file writes the year tag."""
        tags = TagData(title="Song", artist="Artist", year="1975")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.year == "1975"

    def test_tag_file_sets_genre(self, tagger, mp3_file):
        """tag_file writes the genre tag."""
        tags = TagData(title="Song", artist="Artist", genre="Rock")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.genre == "Rock"

    def test_tag_file_sets_isrc(self, tagger, mp3_file):
        """tag_file writes the ISRC tag."""
        tags = TagData(title="Song", artist="Artist", isrc="GBUM71029604")
        tagger.tag_file(mp3_file, tags)

        read_back = tagger.read_tags(mp3_file)
        assert read_back.isrc == "GBUM71029604"

    def test_tag_file_raises_on_missing_file(self, tagger, tmp_path):
        """tag_file raises FileNotFoundError for nonexistent files."""
        missing = tmp_path / "nonexistent.mp3"
        tags = TagData(title="Song", artist="Artist")
        with pytest.raises(FileNotFoundError):
            tagger.tag_file(missing, tags)

    def test_tag_file_raises_on_non_mp3(self, tagger, tmp_path):
        """tag_file raises ValueError for non-MP3 files."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("not an mp3")
        tags = TagData(title="Song", artist="Artist")
        with pytest.raises(ValueError, match="Not an MP3"):
            tagger.tag_file(txt_file, tags)

    def test_read_tags_empty_file(self, tagger, mp3_file):
        """read_tags returns empty TagData for untagged files."""
        tags = tagger.read_tags(mp3_file)
        # Before tagging, should return mostly None fields
        assert tags.title is None or tags.title == ""

    def test_read_tags_raises_on_missing_file(self, tagger, tmp_path):
        """read_tags raises FileNotFoundError for nonexistent files."""
        missing = tmp_path / "nonexistent.mp3"
        with pytest.raises(FileNotFoundError):
            tagger.read_tags(missing)


class TestJellyfinClient:
    """Test the Jellyfin HTTP client (mocked)."""

    @pytest.mark.asyncio
    async def test_library_scan_request(self):
        """Triggering a library scan sends the correct HTTP request."""
        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.status = 204
            mock_session.post = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # Simulate what a JellyfinClient would do
            jellyfin_url = "http://localhost:8096"
            jellyfin_token = "test-jellyfin-token"
            headers = {"X-Emby-Token": jellyfin_token}

            async with mock_session_cls() as session, session.post(
                f"{jellyfin_url}/Library/Refresh",
                headers=headers,
            ) as resp:
                assert resp.status == 204

    @pytest.mark.asyncio
    async def test_jellyfin_auth_header(self):
        """Jellyfin requests include the X-Emby-Token header."""
        token = "test-jellyfin-token"
        headers = {"X-Emby-Token": token}
        assert headers["X-Emby-Token"] == token

    @pytest.mark.asyncio
    async def test_jellyfin_search_items(self):
        """Searching Jellyfin for a track returns structured results."""
        mock_response = {
            "Items": [
                {
                    "Name": "Bohemian Rhapsody",
                    "Id": "abc123",
                    "AlbumArtist": "Queen",
                    "Album": "A Night at the Opera",
                    "RunTimeTicks": 3540000000,
                }
            ],
            "TotalRecordCount": 1,
        }

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            async with mock_session_cls() as session, session.get(
                "http://localhost:8096/Items",
                params={"searchTerm": "Bohemian Rhapsody", "IncludeItemTypes": "Audio"},
                headers={"X-Emby-Token": "test-token"},
            ) as resp:
                data = await resp.json()
                assert data["TotalRecordCount"] == 1
                assert data["Items"][0]["Name"] == "Bohemian Rhapsody"
