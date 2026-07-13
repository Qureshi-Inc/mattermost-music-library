"""Spotify metadata resolver using the Spotify Web API."""

import logging
import os
import re
import time

import aiohttp

from .base import BaseResolver, TrackMetadata

logger = logging.getLogger(__name__)

_SPOTIFY_TRACK_PATTERN = re.compile(
    r"(?:https?://)?open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]+)"
)
_SPOTIFY_URI_PATTERN = re.compile(r"spotify:track:([A-Za-z0-9]+)")

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"


def _extract_track_id(url: str) -> str | None:
    """Extract the Spotify track ID from a URL or URI."""
    match = _SPOTIFY_TRACK_PATTERN.search(url)
    if match:
        return match.group(1)
    match = _SPOTIFY_URI_PATTERN.search(url)
    if match:
        return match.group(1)
    return None


class SpotifyResolver(BaseResolver):
    """Resolve Spotify track URLs to metadata via the Web API.

    Uses the client_credentials OAuth flow which does not require user
    authorization -- suitable for public track metadata lookups.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazily create and return an aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _ensure_token(self) -> str | None:
        """Obtain or refresh the client_credentials access token.

        Returns the token string or None if credentials are missing/invalid.
        """
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        if not self._client_id or not self._client_secret:
            logger.warning("Spotify credentials not configured; cannot authenticate")
            return None

        session = await self._get_session()
        try:
            async with session.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(self._client_id, self._client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("Spotify token request failed (%d): %s", resp.status, body)
                    return None
                data = await resp.json()
                self._access_token = data["access_token"]
                self._token_expires_at = time.time() + data.get("expires_in", 3600)
                return self._access_token
        except Exception as exc:
            logger.error("Spotify token request exception: %s", exc)
            return None

    def can_handle(self, url: str) -> bool:
        """Return True for Spotify track URLs and URIs."""
        return _extract_track_id(url) is not None

    async def resolve(self, url: str) -> TrackMetadata:
        """Fetch track metadata from Spotify Web API."""
        track_id = _extract_track_id(url)
        if not track_id:
            return TrackMetadata(provider="spotify")

        logger.info("Resolving Spotify track: %s", track_id)

        token = await self._ensure_token()
        if not token:
            return await self._oembed_fallback(url, track_id)

        session = await self._get_session()
        try:
            async with session.get(
                f"{_API_BASE}/tracks/{track_id}",
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status == 401:
                    # Token may have expired mid-flight; clear and retry once
                    self._access_token = None
                    token = await self._ensure_token()
                    if not token:
                        return TrackMetadata(provider="spotify", provider_id=track_id)
                    async with session.get(
                        f"{_API_BASE}/tracks/{track_id}",
                        headers={"Authorization": f"Bearer {token}"},
                    ) as retry_resp:
                        if retry_resp.status != 200:
                            logger.warning(
                                "Spotify API returned %d for track %s",
                                retry_resp.status,
                                track_id,
                            )
                            return TrackMetadata(provider="spotify", provider_id=track_id)
                        data = await retry_resp.json()
                elif resp.status != 200:
                    logger.warning(
                        "Spotify API returned %d for track %s", resp.status, track_id
                    )
                    return TrackMetadata(provider="spotify", provider_id=track_id)
                else:
                    data = await resp.json()
        except Exception as exc:
            logger.error("Spotify API request failed: %s", exc)
            return TrackMetadata(provider="spotify", provider_id=track_id)

        # Extract fields from the track object
        title = data.get("name")
        artists = data.get("artists", [])
        artist = ", ".join(a["name"] for a in artists if a.get("name")) or None
        album_obj = data.get("album", {})
        album = album_obj.get("name")
        duration_ms = data.get("duration_ms")
        duration_seconds = duration_ms / 1000.0 if duration_ms is not None else None

        # ISRC is in external_ids
        external_ids = data.get("external_ids", {})
        isrc = external_ids.get("isrc")

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            isrc=isrc,
            provider_id=track_id,
            provider="spotify",
            extra={
                "spotify_uri": data.get("uri"),
                "explicit": data.get("explicit"),
                "popularity": data.get("popularity"),
                "preview_url": data.get("preview_url"),
                "artwork_url": album_obj.get("images", [{}])[0].get("url")
                if album_obj.get("images")
                else None,
                "release_date": album_obj.get("release_date"),
            },
        )

    async def _oembed_fallback(self, url: str, track_id: str) -> TrackMetadata:
        """Use Spotify's page Open Graph metadata to get track info without auth."""
        session = await self._get_session()

        # Fetch the track page for OG metadata
        try:
            track_url = f"https://open.spotify.com/track/{track_id}"
            headers = {"User-Agent": "Mozilla/5.0 (compatible; Slaptastic/1.0)"}
            async with session.get(track_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Spotify page returned %d", resp.status)
                    return TrackMetadata(provider="spotify", provider_id=track_id)
                html = await resp.text()
        except Exception as exc:
            logger.error("Spotify page fetch failed: %s", exc)
            return TrackMetadata(provider="spotify", provider_id=track_id)

        # Parse OG tags
        title = None
        artist = None
        album = None
        year = None
        artwork_url = None

        # og:title = song title
        og_title = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        if og_title:
            title = og_title.group(1)

        # og:description = "Artist · Album · Song · Year"
        og_desc = re.search(r'property="og:description"\s+content="([^"]*)"', html)
        if og_desc:
            parts = [p.strip() for p in og_desc.group(1).split("·")]
            if len(parts) >= 1:
                artist = parts[0]
            if len(parts) >= 2:
                album = parts[1]
            if len(parts) >= 4:
                year = parts[3]

        # og:image = artwork
        og_image = re.search(r'property="og:image"\s+content="([^"]*)"', html)
        if og_image:
            artwork_url = og_image.group(1)

        # Also try oEmbed thumbnail as fallback artwork
        if not artwork_url:
            try:
                oembed_url = f"https://open.spotify.com/oembed?url={track_url}"
                async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        artwork_url = data.get("thumbnail_url")
            except Exception:
                pass

        logger.info("Spotify OG resolved: %s - %s (album=%s)", artist, title, album)

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            provider_id=track_id,
            provider="spotify",
            extra={
                "artwork_url": artwork_url,
                "release_date": year,
            },
        )

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
