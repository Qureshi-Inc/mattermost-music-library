"""Metadata resolvers for extracting track information from music platform URLs.

This package provides async resolvers for YouTube, Spotify, Apple Music, and
MusicBrainz, along with a ResolverRegistry that automatically routes URLs to
the appropriate resolver and optionally enriches results via MusicBrainz
cross-referencing.

Usage:
    from app.resolvers import ResolverRegistry, TrackMetadata

    registry = ResolverRegistry()
    metadata = await registry.resolve("https://open.spotify.com/track/...")
    print(metadata.title, metadata.artist)
    await registry.close()
"""

from .apple_music import AppleMusicResolver
from .base import BaseResolver, TrackMetadata
from .musicbrainz import MusicBrainzResolver
from .registry import ResolverRegistry
from .spotify import SpotifyResolver
from .youtube import YouTubeResolver

__all__ = [
    "AppleMusicResolver",
    "BaseResolver",
    "MusicBrainzResolver",
    "ResolverRegistry",
    "SpotifyResolver",
    "TrackMetadata",
    "YouTubeResolver",
]
