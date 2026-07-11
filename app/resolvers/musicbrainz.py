"""MusicBrainz metadata resolver for cross-referencing and enrichment."""

import logging
import os

import aiohttp

from .base import BaseResolver, TrackMetadata

logger = logging.getLogger(__name__)

_API_BASE = "https://musicbrainz.org/ws/2"

# MusicBrainz requires a descriptive User-Agent per their API policy:
# https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
_USER_AGENT = "Slaptastic/0.1.0 (https://github.com/slaptastic/slaptastic)"


class MusicBrainzResolver(BaseResolver):
    """Resolve tracks via MusicBrainz by ISRC or title+artist search.

    MusicBrainz is primarily used for enrichment -- looking up additional
    metadata (ISRC, release info, recording details) after another resolver
    has provided initial data.

    Respects MusicBrainz rate limiting (1 request/second) by design:
    callers should not invoke this resolver in tight loops.
    """

    def __init__(self, user_agent: str | None = None) -> None:
        self._user_agent = user_agent or os.environ.get(
            "MUSICBRAINZ_USER_AGENT", _USER_AGENT
        )
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazily create and return an aiohttp session with proper headers."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json",
                }
            )
        return self._session

    def can_handle(self, url: str) -> bool:
        """MusicBrainz resolver handles musicbrainz.org URLs.

        For enrichment use cases (ISRC lookup, title+artist search), callers
        should use lookup_by_isrc() or search_by_title_artist() directly.
        """
        return "musicbrainz.org" in url

    async def resolve(self, url: str) -> TrackMetadata:
        """Resolve a MusicBrainz recording URL.

        Extracts the MBID from the URL and fetches recording details.
        """
        # Extract MBID from URL like:
        # https://musicbrainz.org/recording/MBID
        mbid = self._extract_mbid(url)
        if not mbid:
            return TrackMetadata(provider="musicbrainz")

        logger.info("Resolving MusicBrainz recording: %s", mbid)
        return await self._fetch_recording(mbid)

    async def lookup_by_isrc(self, isrc: str) -> TrackMetadata:
        """Look up a recording by its ISRC code.

        Args:
            isrc: International Standard Recording Code (e.g. "USAT21301011").

        Returns:
            TrackMetadata populated from the first matching recording.
        """
        if not isrc:
            return TrackMetadata(provider="musicbrainz")

        logger.info("MusicBrainz ISRC lookup: %s", isrc)
        session = await self._get_session()

        try:
            async with session.get(
                f"{_API_BASE}/isrc/{isrc}",
                params={"inc": "artists+releases", "fmt": "json"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "MusicBrainz ISRC lookup returned %d for %s", resp.status, isrc
                    )
                    return TrackMetadata(provider="musicbrainz", isrc=isrc)
                data = await resp.json()
        except Exception as exc:
            logger.error("MusicBrainz ISRC lookup failed: %s", exc)
            return TrackMetadata(provider="musicbrainz", isrc=isrc)

        recordings = data.get("recordings", [])
        if not recordings:
            return TrackMetadata(provider="musicbrainz", isrc=isrc)

        recording = recordings[0]
        return self._parse_recording(recording, isrc=isrc)

    async def search_by_title_artist(
        self, title: str, artist: str, limit: int = 5
    ) -> TrackMetadata:
        """Search MusicBrainz for a recording by title and artist.

        Args:
            title: Track title to search for.
            artist: Artist name to search for.
            limit: Maximum number of results to consider.

        Returns:
            TrackMetadata from the best matching recording.
        """
        if not title:
            return TrackMetadata(provider="musicbrainz")

        logger.info("MusicBrainz search: title=%r artist=%r", title, artist)
        session = await self._get_session()

        # Build Lucene query
        query_parts = [f'recording:"{title}"']
        if artist:
            query_parts.append(f'artist:"{artist}"')
        query = " AND ".join(query_parts)

        try:
            async with session.get(
                f"{_API_BASE}/recording",
                params={
                    "query": query,
                    "limit": str(limit),
                    "fmt": "json",
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning("MusicBrainz search returned %d", resp.status)
                    return TrackMetadata(provider="musicbrainz")
                data = await resp.json()
        except Exception as exc:
            logger.error("MusicBrainz search failed: %s", exc)
            return TrackMetadata(provider="musicbrainz")

        recordings = data.get("recordings", [])
        if not recordings:
            return TrackMetadata(provider="musicbrainz")

        # Return the highest-scored result
        recording = recordings[0]
        return self._parse_recording(recording)

    async def _fetch_recording(self, mbid: str) -> TrackMetadata:
        """Fetch a recording by its MusicBrainz ID."""
        session = await self._get_session()

        try:
            async with session.get(
                f"{_API_BASE}/recording/{mbid}",
                params={"inc": "artists+releases+isrcs", "fmt": "json"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "MusicBrainz recording fetch returned %d for %s",
                        resp.status,
                        mbid,
                    )
                    return TrackMetadata(provider="musicbrainz", provider_id=mbid)
                data = await resp.json()
        except Exception as exc:
            logger.error("MusicBrainz recording fetch failed: %s", exc)
            return TrackMetadata(provider="musicbrainz", provider_id=mbid)

        return self._parse_recording(data)

    def _parse_recording(
        self, recording: dict, isrc: str | None = None
    ) -> TrackMetadata:
        """Parse a MusicBrainz recording object into TrackMetadata."""
        title = recording.get("title")
        mbid = recording.get("id")

        # Artist credits
        artist_credits = recording.get("artist-credit", [])
        artist_parts = []
        for credit in artist_credits:
            name = credit.get("name") or credit.get("artist", {}).get("name")
            if name:
                artist_parts.append(name)
            joinphrase = credit.get("joinphrase", "")
            if joinphrase:
                artist_parts.append(joinphrase)
        artist = "".join(artist_parts).strip() or None

        # Duration (MusicBrainz stores in milliseconds as "length")
        length_ms = recording.get("length")
        duration_seconds = length_ms / 1000.0 if length_ms is not None else None

        # Album from first release
        releases = recording.get("releases", [])
        album = releases[0].get("title") if releases else None

        # ISRC - from the recording object or passed in
        isrcs = recording.get("isrcs", [])
        resolved_isrc = isrc or (isrcs[0] if isrcs else None)

        return TrackMetadata(
            title=title,
            artist=artist,
            album=album,
            duration_seconds=duration_seconds,
            isrc=resolved_isrc,
            provider_id=mbid,
            provider="musicbrainz",
            extra={
                "mbid": mbid,
                "score": recording.get("score"),
                "disambiguation": recording.get("disambiguation"),
                "release_count": len(releases),
            },
        )

    @staticmethod
    def _extract_mbid(url: str) -> str | None:
        """Extract the recording MBID from a MusicBrainz URL."""
        # URL format: https://musicbrainz.org/recording/{mbid}
        import re

        match = re.search(
            r"musicbrainz\.org/recording/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            url,
        )
        return match.group(1) if match else None

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
