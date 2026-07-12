"""Audio file tagger using mutagen for ID3v2.4 tag manipulation."""

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TSRC,
    TXXX,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3

logger = logging.getLogger(__name__)


@dataclass
class TagData:
    """Data to write as ID3v2.4 tags to an MP3 file."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    year: str | None = None
    genre: str | None = None
    isrc: str | None = None
    artwork_url: str | None = None
    source_url: str | None = None
    youtube_id: str | None = None


class AudioTagger:
    """Tags MP3 files with ID3v2.4 metadata using mutagen.

    All text frames use UTF-8 encoding (encoding=3 in mutagen).
    """

    ENCODING_UTF8 = 3  # mutagen encoding constant for UTF-8

    def tag_file(self, file_path: Path, tags: TagData) -> None:
        """Apply ID3v2.4 tags to an MP3 file.

        Args:
            file_path: Path to the MP3 file to tag.
            tags: TagData instance containing the metadata to write.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file is not a valid MP3.
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not path.suffix.lower() == ".mp3":
            raise ValueError(f"Not an MP3 file: {path}")

        # Validate it's actually an MP3
        try:
            MP3(str(path))
        except Exception as e:
            raise ValueError(f"Cannot read MP3 file: {path} - {e}") from e

        # Load existing ID3 tags or create new ones
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            # No existing ID3 header - create one
            id3 = ID3()

        # Delete all existing tags and start fresh with v2.4
        id3.delete(str(path))
        id3 = ID3()

        # Apply tags using UTF-8 encoding
        if tags.title:
            id3.add(TIT2(encoding=self.ENCODING_UTF8, text=[tags.title]))

        if tags.artist:
            id3.add(TPE1(encoding=self.ENCODING_UTF8, text=[tags.artist]))

        if tags.album:
            id3.add(TALB(encoding=self.ENCODING_UTF8, text=[tags.album]))

        if tags.track_number is not None:
            track_str = str(tags.track_number)
            if tags.total_tracks is not None:
                track_str = f"{tags.track_number}/{tags.total_tracks}"
            id3.add(TRCK(encoding=self.ENCODING_UTF8, text=[track_str]))

        if tags.year:
            id3.add(TDRC(encoding=self.ENCODING_UTF8, text=[tags.year]))

        if tags.genre:
            id3.add(TCON(encoding=self.ENCODING_UTF8, text=[tags.genre]))

        if tags.isrc:
            id3.add(TSRC(encoding=self.ENCODING_UTF8, text=[tags.isrc]))

        if tags.album_artist:
            id3.add(TPE2(encoding=self.ENCODING_UTF8, text=[tags.album_artist]))

        if tags.disc_number is not None:
            id3.add(TXXX(encoding=self.ENCODING_UTF8, desc="DISCNUMBER", text=[str(tags.disc_number)]))

        if tags.source_url:
            id3.add(TXXX(encoding=self.ENCODING_UTF8, desc="SOURCE_URL", text=[tags.source_url]))

        if tags.youtube_id:
            id3.add(TXXX(encoding=self.ENCODING_UTF8, desc="YOUTUBE_ID", text=[tags.youtube_id]))

        # Download and embed artwork
        if tags.artwork_url:
            artwork_data = self._download_artwork(tags.artwork_url)
            if artwork_data:
                id3.add(APIC(
                    encoding=0,
                    mime="image/jpeg",
                    type=3,  # Cover (front)
                    desc="Cover",
                    data=artwork_data,
                ))
                logger.info("Embedded artwork from %s", tags.artwork_url[:80])

        # Save with ID3v2.4
        id3.save(str(path), v2_version=4)

        logger.info(
            "Tagged file",
            extra={
                "file": str(path),
                "title": tags.title,
                "artist": tags.artist,
                "album": tags.album,
            },
        )

    def read_tags(self, file_path: Path) -> TagData:
        """Read ID3 tags from an MP3 file.

        Args:
            file_path: Path to the MP3 file.

        Returns:
            TagData populated with values from the file's ID3 tags.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            return TagData()

        # Parse track number (may be "5" or "5/12")
        track_number = None
        total_tracks = None
        trck = id3.get("TRCK")
        if trck:
            trck_text = str(trck.text[0]) if trck.text else ""
            if "/" in trck_text:
                parts = trck_text.split("/", 1)
                try:
                    track_number = int(parts[0])
                    total_tracks = int(parts[1])
                except (ValueError, IndexError):
                    pass
            elif trck_text.isdigit():
                track_number = int(trck_text)

        return TagData(
            title=self._get_text(id3, "TIT2"),
            artist=self._get_text(id3, "TPE1"),
            album=self._get_text(id3, "TALB"),
            track_number=track_number,
            total_tracks=total_tracks,
            year=self._get_text(id3, "TDRC"),
            genre=self._get_text(id3, "TCON"),
            isrc=self._get_text(id3, "TSRC"),
        )

    @staticmethod
    def _download_artwork(url: str) -> bytes | None:
        """Download artwork image from URL."""
        try:
            # Upscale iTunes artwork to 600x600
            if "itunes.apple.com" in url or "mzstatic.com" in url:
                url = url.replace("100x100", "600x600").replace("60x60", "600x600")
            with httpx.Client(timeout=10) as client:
                resp = client.get(url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    return resp.content
        except Exception as e:
            logger.warning("Failed to download artwork: %s", e)
        return None

    @staticmethod
    def _get_text(id3: ID3, frame_id: str) -> str | None:
        """Extract text from an ID3 frame, returning None if not present."""
        frame = id3.get(frame_id)
        if frame and frame.text:
            text = str(frame.text[0])
            return text if text else None
        return None
