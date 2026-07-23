"""Apple Music metadata resolver using the Apple Music API."""

import logging
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from .base import BaseResolver, TrackMetadata

logger = logging.getLogger(__name__)

# Apple Music URL patterns:
# https://music.apple.com/{storefront}/album/{album-name}/{album-id}?i={track-id}
# https://music.apple.com/{storefront}/song/{song-name}/{track-id}
_APPLE_MUSIC_TRACK_PATTERN = re.compile(
    r"(?:https?://)?music\.apple\.com/([a-z]{2})/(?:album|song)/[^/]+/(\d+)(?:\?i=(\d+))?"
)

# Search URLs: https://music.apple.com/{storefront}/search?term=some+song+name
_APPLE_MUSIC_SEARCH_PATTERN = re.compile(
    r"(?:https?://)?music\.apple\.com/[a-z]{2}/search\b", re.IGNORECASE
)

_API_BASE = "https://api.music.apple.com/v1"


def _extract_search_term(url: str) -> str | None:
    """Extract the search term from an Apple Music search URL.

    e.g. .../search?term=Koi%20Labda%20Symt -> "Koi Labda Symt".
    Returns None if this isn't a search URL or has no term.
    """
    if not _APPLE_MUSIC_SEARCH_PATTERN.search(url):
        return None
    qs = parse_qs(urlparse(url).query)
    term = (qs.get("term") or qs.get("q") or [None])[0]
    if not term:
        return None
    return unquote(term).strip() or None


def _extract_track_info(url: str) -> tuple[str, str] | None:
    """Extract (storefront, track_id) from an Apple Music URL.

    For album URLs with ?i=track_id, the track_id parameter is used.
    For direct song URLs, the ID in the path is used.
    """
    match = _APPLE_MUSIC_TRACK_PATTERN.search(url)
    if not match:
        return None
    storefront = match.group(1)
    # If ?i= parameter exists, that's the track ID
    track_id = match.group(3) or match.group(2)
    return storefront, track_id


class AppleMusicResolver(BaseResolver):
    """Resolve Apple Music URLs to track metadata.

    Requires an Apple Music API developer token (JWT) set via
    APPLE_MUSIC_TOKEN environment variable or passed directly.
    """

    def __init__(self, developer_token: str | None = None) -> None:
        self._token = developer_token or os.environ.get("APPLE_MUSIC_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazily create and return an aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def can_handle(self, url: str) -> bool:
        """Return True for Apple Music track/song URLs and search URLs."""
        return _extract_track_info(url) is not None or _extract_search_term(url) is not None

    async def resolve(self, url: str) -> TrackMetadata:
        """Fetch track metadata from the Apple Music API.

        For search URLs (music.apple.com/../search?term=...), there is no track
        to look up — use the search term as the title so the pipeline searches
        YouTube for it directly.
        """
        search_term = _extract_search_term(url)
        if search_term is not None:
            logger.info("Apple Music search URL; using term as query: %r", search_term)
            return TrackMetadata(title=search_term, provider="apple_music")

        info = _extract_track_info(url)
        if not info:
            return TrackMetadata(provider="apple_music")

        storefront, track_id = info
        logger.info("Resolving Apple Music track: %s (storefront=%s)", track_id, storefront)

        if not self._token:
            logger.info("Apple Music developer token not configured, using iTunes lookup")
            return await self._itunes_lookup(track_id, storefront)

        session = await self._get_session()
        try:
            async with session.get(
                f"{_API_BASE}/catalog/{storefront}/songs/{track_id}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Apple Music API returned %d for track %s", resp.status, track_id
                    )
                    return TrackMetadata(provider="apple_music", provider_id=track_id)
                data = await resp.json()
        except Exception as exc:
            logger.error("Apple Music API request failed: %s", exc)
            return TrackMetadata(provider="apple_music", provider_id=track_id)

        # Parse response
        songs = data.get("data", [])
        if not songs:
            logger.warning("Apple Music returned empty data for track %s", track_id)
            return TrackMetadata(provider="apple_music", provider_id=track_id)

        song = songs[0]
        attributes = song.get("attributes", {})

        title = attributes.get("name")
        artist = attributes.get("artistName")
        album = attributes.get("albumName")
        duration_ms = attributes.get("durationInMillis")
        duration_seconds = duration_ms / 1000.0 if duration_ms is not None else None
        isrc = attributes.get("isrc")

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            isrc=isrc,
            provider_id=track_id,
            provider="apple_music",
            extra={
                "genre": attributes.get("genreNames", [None])[0]
                if attributes.get("genreNames")
                else None,
                "release_date": attributes.get("releaseDate"),
                "artwork_url": attributes.get("artwork", {}).get("url"),
                "composer": attributes.get("composerName"),
                "disc_number": attributes.get("discNumber"),
                "track_number": attributes.get("trackNumber"),
                "content_rating": attributes.get("contentRating"),
            },
        )

    async def _itunes_lookup(self, track_id: str, storefront: str) -> TrackMetadata:
        """Fall back to the free iTunes Lookup API (no auth required)."""
        session = await self._get_session()
        try:
            async with session.get(
                f"https://itunes.apple.com/lookup?id={track_id}&country={storefront}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("iTunes lookup returned %d for track %s", resp.status, track_id)
                    return TrackMetadata(provider="apple_music", provider_id=track_id)
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("iTunes lookup failed: %s", exc)
            return TrackMetadata(provider="apple_music", provider_id=track_id)

        results = data.get("results", [])
        if not results:
            logger.warning("iTunes lookup returned no results for track %s", track_id)
            return TrackMetadata(provider="apple_music", provider_id=track_id)

        track = results[0]
        title = track.get("trackName")
        artist = track.get("artistName")
        album = track.get("collectionName")
        duration_ms = track.get("trackTimeMillis")
        duration_seconds = duration_ms / 1000.0 if duration_ms else None

        logger.info("iTunes lookup resolved: %s - %s", artist, title)

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            provider_id=track_id,
            provider="apple_music",
            extra={
                "genre": track.get("primaryGenreName"),
                "release_date": track.get("releaseDate"),
                "artwork_url": track.get("artworkUrl100"),
                "track_number": track.get("trackNumber"),
                "disc_number": track.get("discNumber"),
            },
        )

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
