"""SoundCloud metadata resolver using yt-dlp.

SoundCloud is natively supported by yt-dlp for both metadata extraction and
audio download, so this resolver mirrors the YouTube one: pull metadata with
download=False, and the pipeline downloads the same URL directly (no YouTube
re-search, since the SoundCloud link already points at the exact audio).
"""

import asyncio
import logging
import re

from .base import BaseResolver, TrackMetadata

logger = logging.getLogger(__name__)

# Patterns that identify SoundCloud URLs.
# Track URLs look like soundcloud.com/<artist>/<track>; also handle the mobile
# host (m.soundcloud.com), the api/embed hosts, and on.soundcloud.com short links.
_SOUNDCLOUD_PATTERNS = [
    re.compile(r"(?:https?://)?(?:www\.|m\.)?soundcloud\.com/[^/\s]+/[^/\s]+"),
    re.compile(r"(?:https?://)?on\.soundcloud\.com/[A-Za-z0-9]+"),
    re.compile(r"(?:https?://)?snd\.sc/[A-Za-z0-9]+"),
]


def matches(url: str) -> bool:
    """Return True if the URL is a SoundCloud link."""
    return any(p.search(url) for p in _SOUNDCLOUD_PATTERNS)


class SoundCloudResolver(BaseResolver):
    """Resolve SoundCloud URLs to track metadata using yt-dlp."""

    def can_handle(self, url: str) -> bool:
        """Return True for soundcloud.com / on.soundcloud.com URLs."""
        return matches(url)

    async def resolve(self, url: str) -> TrackMetadata:
        """Extract metadata from a SoundCloud URL via yt-dlp.

        Runs yt-dlp in a thread executor since it performs synchronous I/O.
        """
        logger.info("Resolving SoundCloud URL: %s", url)

        try:
            info = await asyncio.to_thread(self._extract_info, url)
        except Exception as exc:
            logger.warning("yt-dlp extraction failed for %s: %s", url, exc)
            return TrackMetadata(provider="soundcloud")

        if info is None:
            return TrackMetadata(provider="soundcloud")

        # A SoundCloud "set" (playlist) URL yields entries; take the first track
        # so a single shared set link still resolves to something playable.
        if info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                return TrackMetadata(provider="soundcloud")
            info = entries[0]

        title = info.get("track") or info.get("title")
        artist = (
            info.get("artist")
            or info.get("uploader")
            or info.get("creator")
            or info.get("channel")
        )
        album = info.get("album")
        duration = info.get("duration")

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=float(duration) if duration is not None else None,
            isrc=info.get("isrc"),  # some SoundCloud uploads carry an ISRC
            provider_id=str(info.get("id")) if info.get("id") is not None else None,
            provider="soundcloud",
            extra={
                k: info.get(k)
                for k in ("thumbnail", "webpage_url", "upload_date", "genre", "tags")
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
            "noplaylist": True,
            "socket_timeout": 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)  # type: ignore[no-any-return]
