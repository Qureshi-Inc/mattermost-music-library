"""DuplicateDetector - checks if a track already exists in the library."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.models.track import Track

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DuplicateDetector:
    """Detects duplicate tracks to avoid re-downloading existing content.

    Checks multiple criteria in order of specificity:
    1. ISRC (International Standard Recording Code) - most reliable
    2. Provider ID (spotify_id, apple_music_id)
    3. Normalized title + artist + duration (fuzzy, within 3s tolerance)
    """

    # Patterns to strip during normalization
    _FEAT_PATTERN = re.compile(
        r"\s*[\(\[]*\s*(?:feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]*",
        re.IGNORECASE,
    )
    _PARENTHETICAL_PATTERN = re.compile(
        r"\s*[\(\[]\s*(?:remaster(?:ed)?|deluxe|bonus|anniversary|edition|version"
        r"|remix(?:ed)?|radio edit|single|extended|acoustic|stripped)\s*[^\)\]]*[\)\]]",
        re.IGNORECASE,
    )
    _EXTRA_WHITESPACE = re.compile(r"\s+")

    # Duration tolerance in seconds for fuzzy matching
    DURATION_TOLERANCE_SECONDS: int = 3

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with an async database session.

        Args:
            session: SQLAlchemy async session for database queries.
        """
        self.session = session

    async def find_duplicate(
        self,
        *,
        title: str,
        artist: str,
        duration_seconds: int | None = None,
        isrc: str | None = None,
        spotify_id: str | None = None,
        apple_music_id: str | None = None,
    ) -> Track | None:
        """Check if a track already exists in the library.

        Checks in order of reliability:
        1. ISRC match
        2. Provider ID match (Spotify or Apple Music)
        3. Normalized title + artist + duration (within tolerance)

        Returns the existing Track if a duplicate is found, None otherwise.
        """
        # Check 1: ISRC (most reliable identifier)
        if isrc:
            existing = await self._find_by_isrc(isrc)
            if existing:
                logger.info("Duplicate found by ISRC %s: %s", isrc, existing)
                return existing

        # Check 2: Provider IDs
        if spotify_id:
            existing = await self._find_by_spotify_id(spotify_id)
            if existing:
                logger.info("Duplicate found by Spotify ID %s: %s", spotify_id, existing)
                return existing

        if apple_music_id:
            existing = await self._find_by_apple_music_id(apple_music_id)
            if existing:
                logger.info(
                    "Duplicate found by Apple Music ID %s: %s", apple_music_id, existing
                )
                return existing

        # Check 3: Normalized title + artist + duration
        existing = await self._find_by_normalized_metadata(title, artist, duration_seconds)
        if existing:
            logger.info(
                "Duplicate found by metadata match: %s - %s (duration within %ds)",
                artist,
                title,
                self.DURATION_TOLERANCE_SECONDS,
            )
            return existing

        return None

    async def _find_by_isrc(self, isrc: str) -> Track | None:
        """Look up a track by ISRC."""
        stmt = select(Track).where(Track.isrc == isrc).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_by_spotify_id(self, spotify_id: str) -> Track | None:
        """Look up a track by Spotify ID."""
        stmt = select(Track).where(Track.spotify_id == spotify_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_by_apple_music_id(self, apple_music_id: str) -> Track | None:
        """Look up a track by Apple Music ID."""
        stmt = select(Track).where(Track.apple_music_id == apple_music_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_by_normalized_metadata(
        self, title: str, artist: str, duration_seconds: int | None
    ) -> Track | None:
        """Look up a track by normalized title + artist + duration.

        Uses case-insensitive comparison on normalized strings and allows
        a duration tolerance of DURATION_TOLERANCE_SECONDS.
        """
        normalized_title = self.normalize(title)
        normalized_artist = self.normalize(artist)

        # Build query using SQL lower() for case-insensitive comparison
        stmt = select(Track).where(
            func.lower(Track.artist) == normalized_artist,
            func.lower(Track.title) == normalized_title,
        )

        # Add duration filter if we have duration info
        if duration_seconds is not None:
            stmt = stmt.where(
                Track.duration_seconds.isnot(None),
                Track.duration_seconds >= duration_seconds - self.DURATION_TOLERANCE_SECONDS,
                Track.duration_seconds <= duration_seconds + self.DURATION_TOLERANCE_SECONDS,
            )

        stmt = stmt.limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    def normalize(cls, text: str) -> str:
        """Normalize a track title or artist name for comparison.

        Normalization steps:
        1. Lowercase
        2. Strip feat./ft./featuring and associated artist names
        3. Strip parenthetical remix/remaster/edition info
        4. Collapse extra whitespace
        5. Strip leading/trailing whitespace
        """
        result = text.lower()
        result = cls._FEAT_PATTERN.sub("", result)
        result = cls._PARENTHETICAL_PATTERN.sub("", result)
        result = cls._EXTRA_WHITESPACE.sub(" ", result)
        return result.strip()
