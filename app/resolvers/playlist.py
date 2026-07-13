"""Playlist resolver — fetches track lists from Spotify and Apple Music playlists."""

import logging
import re
import time
from dataclasses import dataclass

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

SPOTIFY_PLAYLIST_PATTERN = re.compile(
    r"https?://open\.spotify\.com/(?:intl-[a-z]+/)?playlist/([A-Za-z0-9]+)"
)
APPLE_PLAYLIST_PATTERN = re.compile(
    r"https?://music\.apple\.com/([a-z]{2})/playlist/[^/]+/(pl\.[A-Za-z0-9-]+)"
)


@dataclass
class PlaylistTrack:
    """A single track from a playlist."""

    title: str
    artist: str
    album: str | None = None
    duration_seconds: float | None = None
    isrc: str | None = None
    spotify_id: str | None = None
    apple_music_id: str | None = None
    artwork_url: str | None = None


@dataclass
class PlaylistInfo:
    """Playlist metadata and tracks."""

    name: str
    owner: str | None = None
    track_count: int = 0
    tracks: list[PlaylistTrack] | None = None
    platform: str = "unknown"


def is_playlist_url(url: str) -> bool:
    """Check if a URL is a Spotify or Apple Music playlist."""
    return bool(SPOTIFY_PLAYLIST_PATTERN.search(url) or APPLE_PLAYLIST_PATTERN.search(url))


async def resolve_playlist(url: str) -> PlaylistInfo | None:
    """Resolve a playlist URL to its track list."""
    spotify_match = SPOTIFY_PLAYLIST_PATTERN.search(url)
    if spotify_match:
        return await _resolve_spotify_playlist(spotify_match.group(1))

    apple_match = APPLE_PLAYLIST_PATTERN.search(url)
    if apple_match:
        storefront = apple_match.group(1)
        playlist_id = apple_match.group(2)
        return await _resolve_apple_music_playlist(storefront, playlist_id)

    return None


# --- Spotify ---

_spotify_token: str | None = None
_spotify_token_expires: float = 0.0


async def _get_spotify_token() -> str | None:
    """Get a Spotify client credentials token."""
    global _spotify_token, _spotify_token_expires

    if _spotify_token and time.time() < _spotify_token_expires - 60:
        return _spotify_token

    settings = get_settings()
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        logger.warning("Spotify credentials not configured for playlist resolution")
        return None

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=aiohttp.BasicAuth(settings.spotify_client_id, settings.spotify_client_secret),
        ) as resp:
            if resp.status != 200:
                logger.error("Spotify token request failed: %d", resp.status)
                return None
            data = await resp.json()
            _spotify_token = data["access_token"]
            _spotify_token_expires = time.time() + data.get("expires_in", 3600)
            return _spotify_token


async def _resolve_spotify_playlist(playlist_id: str) -> PlaylistInfo | None:
    """Fetch all tracks from a Spotify playlist."""
    token = await _get_spotify_token()
    if not token:
        return None

    async with aiohttp.ClientSession() as session:
        # Get playlist info
        async with session.get(
            f"https://api.spotify.com/v1/playlists/{playlist_id}",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status != 200:
                logger.warning("Spotify playlist API returned %d, trying embed fallback", resp.status)
                return await _spotify_embed_fallback(playlist_id, token)
            data = await resp.json()

    name = data.get("name", "Unknown Playlist")
    owner = data.get("owner", {}).get("display_name")
    tracks_data = data.get("tracks", {})
    total = tracks_data.get("total", 0)

    tracks: list[PlaylistTrack] = []
    for item in tracks_data.get("items", []):
        track = item.get("track")
        if not track or track.get("is_local"):
            continue

        artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
        album_obj = track.get("album", {})
        duration_ms = track.get("duration_ms")
        isrc = track.get("external_ids", {}).get("isrc")
        artwork = album_obj.get("images", [{}])[0].get("url") if album_obj.get("images") else None

        tracks.append(PlaylistTrack(
            title=track.get("name", "Unknown"),
            artist=artists or "Unknown",
            album=album_obj.get("name"),
            duration_seconds=duration_ms / 1000.0 if duration_ms else None,
            isrc=isrc,
            spotify_id=track.get("id"),
            artwork_url=artwork,
        ))

    # Handle pagination if more than 100 tracks
    next_url = tracks_data.get("next")
    while next_url:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                next_url,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    break
                page = await resp.json()

        for item in page.get("items", []):
            track = item.get("track")
            if not track or track.get("is_local"):
                continue
            artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
            album_obj = track.get("album", {})
            duration_ms = track.get("duration_ms")
            isrc = track.get("external_ids", {}).get("isrc")
            artwork = album_obj.get("images", [{}])[0].get("url") if album_obj.get("images") else None
            tracks.append(PlaylistTrack(
                title=track.get("name", "Unknown"),
                artist=artists or "Unknown",
                album=album_obj.get("name"),
                duration_seconds=duration_ms / 1000.0 if duration_ms else None,
                isrc=isrc,
                spotify_id=track.get("id"),
                artwork_url=artwork,
            ))
        next_url = page.get("next")

    # If API returned name but no tracks, try embed fallback
    if not tracks and total > 0:
        logger.warning("Spotify API returned name but 0 tracks (total=%d), trying embed", total)
        fallback = await _spotify_embed_fallback(playlist_id, token)
        if fallback and fallback.tracks:
            return fallback

    logger.info("Resolved Spotify playlist: %s (%d tracks)", name, len(tracks))
    return PlaylistInfo(name=name, owner=owner, track_count=total, tracks=tracks, platform="spotify")


async def _spotify_embed_fallback(playlist_id: str, token: str) -> PlaylistInfo | None:
    """Scrape playlist tracks from Spotify's embed page or use search-based approach."""
    async with aiohttp.ClientSession() as session:
        # Get playlist page OG data for the name
        url = f"https://open.spotify.com/playlist/{playlist_id}"
        async with session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Slaptastic/1.0)"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

    import re

    # Get playlist name
    name_match = re.search(r'property="og:title"\s+content="([^"]*)"', html)
    name = name_match.group(1) if name_match else "Unknown Playlist"

    # Extract track links from the HTML (Spotify embeds track IDs in the page)
    track_ids = re.findall(r'/track/([A-Za-z0-9]{22})', html)
    track_ids = list(dict.fromkeys(track_ids))  # Deduplicate preserving order

    if not track_ids:
        logger.warning("Embed fallback: no track IDs found in page for %s", playlist_id)
        return PlaylistInfo(name=name, track_count=0, tracks=[], platform="spotify")

    # Fetch metadata for each track via the API (tracks endpoint works)
    tracks: list[PlaylistTrack] = []
    async with aiohttp.ClientSession() as session:
        # Batch fetch tracks (max 50 per request)
        for i in range(0, len(track_ids), 50):
            batch = track_ids[i:i + 50]
            async with session.get(
                "https://api.spotify.com/v1/tracks",
                headers={"Authorization": f"Bearer {token}"},
                params={"ids": ",".join(batch)},
            ) as resp:
                if resp.status != 200:
                    logger.warning("Batch track fetch failed: %d", resp.status)
                    continue
                data = await resp.json()

            for track in data.get("tracks", []):
                if not track:
                    continue
                artists = ", ".join(a["name"] for a in track.get("artists", []) if a.get("name"))
                album_obj = track.get("album", {})
                duration_ms = track.get("duration_ms")
                isrc = track.get("external_ids", {}).get("isrc")
                artwork = album_obj.get("images", [{}])[0].get("url") if album_obj.get("images") else None
                tracks.append(PlaylistTrack(
                    title=track.get("name", "Unknown"),
                    artist=artists or "Unknown",
                    album=album_obj.get("name"),
                    duration_seconds=duration_ms / 1000.0 if duration_ms else None,
                    isrc=isrc,
                    spotify_id=track.get("id"),
                    artwork_url=artwork,
                ))

    logger.info("Embed fallback resolved: %s (%d tracks)", name, len(tracks))
    return PlaylistInfo(name=name, track_count=len(tracks), tracks=tracks, platform="spotify")


# --- Apple Music ---

async def _resolve_apple_music_playlist(storefront: str, playlist_id: str) -> PlaylistInfo | None:
    """Fetch tracks from an Apple Music playlist using the public catalog API."""
    # Apple Music playlists can be fetched via iTunes RSS or the catalog API
    # The catalog API needs a developer token, but we can try the public storefront endpoint
    settings = get_settings()

    async with aiohttp.ClientSession() as session:
        # Try public catalog endpoint (works for public/curated playlists)
        url = f"https://api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}"
        headers = {}
        if settings.apple_music_token:
            headers["Authorization"] = f"Bearer {settings.apple_music_token}"
        else:
            # Without auth, try the embed endpoint
            return await _apple_music_embed_fallback(storefront, playlist_id)

        async with session.get(url, headers=headers, params={"include": "tracks"}) as resp:
            if resp.status != 200:
                logger.warning("Apple Music playlist API returned %d, trying fallback", resp.status)
                return await _apple_music_embed_fallback(storefront, playlist_id)
            data = await resp.json()

    playlists = data.get("data", [])
    if not playlists:
        return None

    playlist = playlists[0]
    attrs = playlist.get("attributes", {})
    name = attrs.get("name", "Unknown Playlist")

    tracks: list[PlaylistTrack] = []
    track_data = playlist.get("relationships", {}).get("tracks", {}).get("data", [])
    for t in track_data:
        t_attrs = t.get("attributes", {})
        tracks.append(PlaylistTrack(
            title=t_attrs.get("name", "Unknown"),
            artist=t_attrs.get("artistName", "Unknown"),
            album=t_attrs.get("albumName"),
            duration_seconds=(t_attrs.get("durationInMillis", 0) or 0) / 1000.0,
            isrc=t_attrs.get("isrc"),
            apple_music_id=t.get("id"),
            artwork_url=t_attrs.get("artwork", {}).get("url", "").replace("{w}x{h}", "600x600"),
        ))

    logger.info("Resolved Apple Music playlist: %s (%d tracks)", name, len(tracks))
    return PlaylistInfo(name=name, track_count=len(tracks), tracks=tracks, platform="apple_music")


async def _apple_music_embed_fallback(storefront: str, playlist_id: str) -> PlaylistInfo | None:
    """Fallback: scrape Apple Music playlist page for track info."""
    url = f"https://music.apple.com/{storefront}/playlist/{playlist_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Slaptastic/1.0)"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Apple Music playlist page returned %d", resp.status)
                return None
            html = await resp.text()

    # Extract playlist name from og:title
    name_match = re.search(r'property="og:title"\s+content="([^"]*)"', html)
    name = name_match.group(1) if name_match else "Unknown Playlist"

    logger.warning("Apple Music playlist fallback: got name '%s' but cannot extract tracks without developer token", name)
    return PlaylistInfo(name=name, track_count=0, tracks=[], platform="apple_music")
