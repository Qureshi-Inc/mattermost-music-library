"""YouTube metadata resolver using yt-dlp."""

import asyncio
import logging
import re
from urllib.parse import parse_qs, urlparse

from .base import BaseResolver, TrackMetadata

logger = logging.getLogger(__name__)

# Patterns that identify YouTube URLs
_YOUTUBE_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/shorts/"),
    re.compile(r"(?:https?://)?youtu\.be/"),
    re.compile(r"(?:https?://)?(?:www\.)?youtube\.com/embed/"),
    re.compile(r"(?:https?://)?music\.youtube\.com/watch\?"),
]


def _extract_video_id(url: str) -> str | None:
    """Extract the video ID from a YouTube URL."""
    parsed = urlparse(url)

    if parsed.hostname in ("youtu.be",):
        # youtu.be/VIDEO_ID
        return parsed.path.lstrip("/").split("/")[0] or None

    if parsed.hostname in ("www.youtube.com", "youtube.com", "music.youtube.com"):
        if "/watch" in parsed.path:
            qs = parse_qs(parsed.query)
            ids = qs.get("v")
            return ids[0] if ids else None
        # /embed/VIDEO_ID or /shorts/VIDEO_ID
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("embed", "shorts"):
            return parts[1]

    return None


class YouTubeResolver(BaseResolver):
    """Resolve YouTube URLs to track metadata using yt-dlp.

    Uses yt-dlp's extract_info with download=False to pull metadata
    without downloading the actual media file.
    """

    def can_handle(self, url: str) -> bool:
        """Return True for youtube.com and youtu.be URLs."""
        return any(pattern.search(url) for pattern in _YOUTUBE_PATTERNS)

    async def resolve(self, url: str) -> TrackMetadata:
        """Extract metadata from a YouTube URL via yt-dlp.

        Runs yt-dlp in a thread executor since it performs synchronous I/O.
        """
        video_id = _extract_video_id(url)
        logger.info("Resolving YouTube URL: %s (id=%s)", url, video_id)

        try:
            info = await asyncio.to_thread(self._extract_info, url)
        except Exception as exc:
            logger.warning("yt-dlp extraction failed for %s: %s", url, exc)
            return TrackMetadata(
                provider="youtube",
                provider_id=video_id,
            )

        if info is None:
            return TrackMetadata(provider="youtube", provider_id=video_id)

        # yt-dlp populates various fields; artist can be in several places
        title = info.get("track") or info.get("title")
        artist = (
            info.get("artist")
            or info.get("creator")
            or info.get("uploader")
            or info.get("channel")
        )
        album = info.get("album")
        duration = info.get("duration")  # seconds as float or int

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=float(duration) if duration is not None else None,
            isrc=None,  # YouTube does not expose ISRC
            provider_id=info.get("id") or video_id,
            provider="youtube",
            extra={
                k: info.get(k)
                for k in ("thumbnail", "view_count", "upload_date", "categories", "tags")
                if info.get(k) is not None
            },
        )

    @staticmethod
    def _extract_info(url: str) -> dict | None:
        """Synchronous yt-dlp metadata extraction (no download)."""
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "nocheckcertificate": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)  # type: ignore[no-any-return]
