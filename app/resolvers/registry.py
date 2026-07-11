"""Resolver registry that routes URLs to the appropriate resolver."""

import logging

from .apple_music import AppleMusicResolver
from .base import BaseResolver, TrackMetadata
from .musicbrainz import MusicBrainzResolver
from .spotify import SpotifyResolver
from .youtube import YouTubeResolver

logger = logging.getLogger(__name__)


class ResolverRegistry:
    """Central registry that maps URLs to the appropriate metadata resolver.

    Usage:
        registry = ResolverRegistry()
        metadata = await registry.resolve("https://open.spotify.com/track/...")

    The registry automatically selects the correct resolver based on URL
    patterns. If no specific resolver matches, it falls back to the YouTube
    resolver for youtube.com/youtu.be URLs.

    After resolving, the registry can optionally enrich metadata by
    cross-referencing MusicBrainz (e.g., to find an ISRC when the primary
    resolver didn't provide one).
    """

    def __init__(
        self,
        *,
        spotify_client_id: str | None = None,
        spotify_client_secret: str | None = None,
        apple_music_token: str | None = None,
        musicbrainz_user_agent: str | None = None,
        enrich_with_musicbrainz: bool = True,
    ) -> None:
        """Initialize the registry with resolver instances.

        Args:
            spotify_client_id: Spotify API client ID.
            spotify_client_secret: Spotify API client secret.
            apple_music_token: Apple Music developer token (JWT).
            musicbrainz_user_agent: Custom User-Agent for MusicBrainz API.
            enrich_with_musicbrainz: Whether to cross-reference MusicBrainz
                for additional metadata after primary resolution.
        """
        self._youtube = YouTubeResolver()
        self._spotify = SpotifyResolver(
            client_id=spotify_client_id,
            client_secret=spotify_client_secret,
        )
        self._apple_music = AppleMusicResolver(developer_token=apple_music_token)
        self._musicbrainz = MusicBrainzResolver(user_agent=musicbrainz_user_agent)
        self._enrich = enrich_with_musicbrainz

        # Ordered list of resolvers; first match wins
        self._resolvers: list[BaseResolver] = [
            self._spotify,
            self._apple_music,
            self._musicbrainz,
            self._youtube,  # YouTube is last as a catch-all for its URLs
        ]

    def get_resolver(self, url: str) -> BaseResolver | None:
        """Find the resolver that can handle the given URL.

        Args:
            url: The URL to find a resolver for.

        Returns:
            The matching resolver or None if no resolver can handle the URL.
        """
        for resolver in self._resolvers:
            if resolver.can_handle(url):
                return resolver
        return None

    async def resolve(self, url: str) -> TrackMetadata:
        """Resolve a URL to track metadata using the appropriate resolver.

        Selects the correct resolver based on URL patterns, resolves the
        metadata, then optionally enriches it via MusicBrainz.

        Args:
            url: A music platform URL to resolve.

        Returns:
            TrackMetadata with as many fields populated as possible.
        """
        url = url.strip()
        resolver = self.get_resolver(url)

        if resolver is None:
            logger.warning("No resolver found for URL: %s", url)
            return TrackMetadata()

        logger.info(
            "Resolving URL with %s: %s",
            type(resolver).__name__,
            url,
        )

        metadata = await resolver.resolve(url)

        # Enrich with MusicBrainz if enabled and we have enough info
        if self._enrich and resolver is not self._musicbrainz:
            metadata = await self._enrich_metadata(metadata)

        return metadata

    async def _enrich_metadata(self, metadata: TrackMetadata) -> TrackMetadata:
        """Enrich metadata by cross-referencing MusicBrainz.

        If the metadata has an ISRC, look it up directly. Otherwise, search
        by title + artist. Merges any additional fields found.
        """
        if not metadata.has_minimum:
            return metadata

        try:
            if metadata.isrc:
                mb_metadata = await self._musicbrainz.lookup_by_isrc(metadata.isrc)
            elif metadata.title:
                mb_metadata = await self._musicbrainz.search_by_title_artist(
                    title=metadata.title,
                    artist=metadata.artist or "",
                )
            else:
                return metadata
        except Exception as exc:
            logger.debug("MusicBrainz enrichment failed: %s", exc)
            return metadata

        if not mb_metadata.has_minimum:
            return metadata

        # Merge: primary metadata takes precedence, MusicBrainz fills gaps
        return metadata.merge(mb_metadata)

    async def close(self) -> None:
        """Close all resolver sessions."""
        for resolver in self._resolvers:
            try:
                await resolver.close()
            except Exception as exc:
                logger.debug("Error closing %s: %s", type(resolver).__name__, exc)
