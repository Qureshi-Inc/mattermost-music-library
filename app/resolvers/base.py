"""Abstract base resolver and TrackMetadata dataclass."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TrackMetadata:
    """Unified track metadata returned by all resolvers.

    Fields may be None when a resolver cannot determine the value.
    Partial metadata is always preferred over raising an error.
    """

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration_seconds: float | None = None
    isrc: str | None = None
    provider_id: str | None = None
    provider: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def has_minimum(self) -> bool:
        """Return True if at least title or provider_id is populated."""
        return bool(self.title or self.provider_id)

    def merge(self, other: "TrackMetadata") -> "TrackMetadata":
        """Merge another TrackMetadata into this one, filling in None fields.

        Fields already set on self are preserved. Only None fields are filled
        from other. Returns a new TrackMetadata instance.
        """
        return TrackMetadata(
            title=self.title or other.title,
            artist=self.artist or other.artist,
            album=self.album or other.album,
            duration_seconds=self.duration_seconds or other.duration_seconds,
            isrc=self.isrc or other.isrc,
            provider_id=self.provider_id or other.provider_id,
            provider=self.provider or other.provider,
            extra={**other.extra, **self.extra},
        )


class BaseResolver(ABC):
    """Abstract base class for all metadata resolvers.

    Subclasses must implement:
        - resolve(url) -> TrackMetadata
        - can_handle(url) -> bool
    """

    @abstractmethod
    async def resolve(self, url: str) -> TrackMetadata:
        """Resolve a URL to track metadata.

        Implementations should never raise on expected errors (network issues,
        missing data, rate limits). Instead, return a TrackMetadata with as many
        fields populated as possible.

        Args:
            url: The music platform URL to resolve.

        Returns:
            TrackMetadata with whatever fields could be extracted.
        """
        ...

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this resolver can handle the given URL.

        Args:
            url: The URL to test.

        Returns:
            True if this resolver knows how to extract metadata from this URL.
        """
        ...

    async def close(self) -> None:  # noqa: B027
        """Clean up any resources (sessions, connections).

        Subclasses should override this if they manage aiohttp sessions or
        other async resources.
        """
