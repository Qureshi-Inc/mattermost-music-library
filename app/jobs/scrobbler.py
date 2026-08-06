"""Scrobble-based auto-add.

Polls multi-scrobbler for what each friend has been listening to and, once a
track crosses a play-count threshold, enqueues it into the normal slaptastic
pipeline (download -> shared library -> that friend's playlist -> optional
Mattermost announce).

Data flow:
    friends' Spotify/Apple/Last.fm -> multi-scrobbler -> Maloja (durable store)
    slaptastic (this module) polls multi-scrobbler per-source recent plays,
    counts them per (friend, artist, track) in its own DB, and acts on the
    threshold. Maloja is kept for durable history + dashboard stats.

Why count here instead of trusting Maloja counts: Maloja pools all users into
one library, so it can't tell us WHO played a track. multi-scrobbler exposes
per-source (per-friend) plays, which is what we need for per-friend playlists
and "moose has been playing it" announcements.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Normalize a track/artist string for stable de-duplication keys."""
    return " ".join((s or "").strip().lower().split())


class ScrobbleWatcher:
    """Polls multi-scrobbler and auto-adds tracks that cross the play threshold.

    State (play counts + which tracks we've already added) lives in the
    `scrobble_plays` table so it survives restarts and we never double-add.
    """

    def __init__(self, queue, pipeline=None) -> None:
        self._settings = get_settings()
        self.queue = queue
        self.pipeline = pipeline
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # --- multi-scrobbler API ------------------------------------------------

    async def _list_source_components(self) -> list[dict]:
        """Return multi-scrobbler source components (one per friend)."""
        session = await self._get_session()
        url = f"{self._settings.multiscrobbler_url}/api/components"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("multi-scrobbler /components returned %d", resp.status)
                    return []
                data = await resp.json()
        except Exception as exc:
            logger.warning("multi-scrobbler unreachable: %s", exc)
            return []
        # Only "source" components represent a friend's listening feed.
        return [c for c in data if c.get("mode") == "source"]

    async def _recent_plays(self, component_id: int) -> list[dict]:
        """Return recent plays for one source component.

        multi-scrobbler exposes `/api/components/<numeric-id>/plays` (it rejects
        the name with "Component id must be a number"). The response is
        `{"data": [ {play: {data: {track, artists:[{name}], playDate}}} ], meta}`.
        Returns the raw list under `data`.
        """
        session = await self._get_session()
        url = f"{self._settings.multiscrobbler_url}/api/components/{component_id}/plays"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.debug("plays for id=%s returned %d", component_id, resp.status)
                    return []
                body = await resp.json()
        except Exception as exc:
            logger.debug("plays fetch failed for id=%s: %s", component_id, exc)
            return []
        if isinstance(body, dict):
            return body.get("data") or []
        return body if isinstance(body, list) else []

    # --- Core loop ----------------------------------------------------------

    async def poll_once(self) -> int:
        """Run one polling pass. Returns the number of tracks enqueued."""
        if not self._settings.scrobbler_enabled:
            return 0

        sources = await self._list_source_components()
        if not sources:
            logger.debug("No scrobble sources connected yet")
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self._settings.scrobble_lookback_days
        )
        threshold = self._settings.scrobble_add_threshold
        enqueued = 0

        for src in sources:
            source_name = src.get("name") or src.get("uid") or str(src.get("id"))
            component_id = src.get("id")
            if component_id is None:
                continue
            plays = await self._recent_plays(component_id)
            # Tally plays within the lookback window per (artist, track).
            counts: dict[tuple[str, str], dict] = {}
            for entry in plays:
                # Each entry: { play: { data: {track, artists:[{name}], ...} }, playedAt }
                d = ((entry.get("play") or {}).get("data")) or entry.get("data") or {}
                track = d.get("track")
                artists = d.get("artists") or []
                # artists is a list of {"name": ...} objects (or plain strings).
                artist = None
                if artists:
                    first = artists[0]
                    artist = first.get("name") if isinstance(first, dict) else first
                artist = artist or d.get("artist")
                if not track or not artist:
                    continue
                # Filter by play date when present.
                pd = (
                    d.get("playDate")
                    or entry.get("playedAt")
                    or d.get("playDateCompleted")
                )
                if pd and not self._within(pd, cutoff):
                    continue
                key = (_norm(artist), _norm(track))
                slot = counts.setdefault(
                    key, {"artist": artist, "track": track, "n": 0}
                )
                slot["n"] += 1

            for (nart, ntrk), info in counts.items():
                if info["n"] < threshold:
                    continue
                added = await self._maybe_enqueue(
                    source_name=source_name,
                    artist=info["artist"],
                    track=info["track"],
                    plays=info["n"],
                    norm_key=f"{nart}||{ntrk}",
                )
                if added:
                    enqueued += 1

        if enqueued:
            logger.info("Scrobble watcher enqueued %d new track(s)", enqueued)
        return enqueued

    @staticmethod
    def _within(play_date: str, cutoff: datetime) -> bool:
        """True if an ISO-ish play date is at/after the cutoff."""
        try:
            dt = datetime.fromisoformat(str(play_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except Exception:
            # If we can't parse it, don't exclude it.
            return True

    async def _maybe_enqueue(
        self, source_name: str, artist: str, track: str, plays: int, norm_key: str
    ) -> bool:
        """Enqueue a track once, recording it so we never double-add.

        Returns True if a new job was created.
        """
        from app.jobs.scrobble_state import already_added, mark_added
        from app.models.job import SourcePlatform

        if await already_added(source_name, norm_key):
            return False

        # Resolve the friend's Mattermost user id (source name == MM username by
        # convention set up when they connect). Fall back to the raw name.
        mm_user_id = await self._resolve_mm_user_id(source_name)

        # Use a search: URL so the existing resolver/search pipeline finds the
        # best match on Spotify/Apple/YouTube for "artist - track".
        search_url = f"search:{artist} - {track}"
        job = await self.queue.create_job(
            url=search_url,
            source_platform=SourcePlatform.UNKNOWN,
            mattermost_post_id=None,
            mattermost_channel_id=self._settings.mattermost_channel,
            requester_user_id=mm_user_id,
        )
        await mark_added(source_name, norm_key, artist, track, plays)
        logger.info(
            "Auto-added from scrobbles: %s - %s (%d plays by %s)",
            artist, track, plays, source_name,
        )

        # Announce in Mattermost (best-effort).
        if self._settings.scrobble_announce_in_mattermost and self.pipeline is not None:
            await self._announce(source_name, artist, track, plays)
        return bool(job)

    async def _resolve_mm_user_id(self, source_name: str) -> str | None:
        """Map a scrobble source name to a Mattermost user id.

        Convention: friends name their multi-scrobbler source after their
        Mattermost username. We look it up; if not found, store None so the
        pipeline still adds to the shared library.
        """
        settings = self._settings
        if not settings.mattermost_url or not settings.mattermost_token:
            return None
        session = await self._get_session()
        url = f"{settings.mattermost_url}/api/v4/users/username/{source_name}"
        try:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {settings.mattermost_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("id")
        except Exception:
            return None

    async def _announce(self, source_name: str, artist: str, track: str, plays: int) -> None:
        """Post a friendly 'trending' note in the channel (best-effort)."""
        try:
            mm = getattr(self.pipeline, "mattermost", None)
            if mm is None or not self._settings.mattermost_channel:
                return
            msg = (
                f"🎧 Auto-added **{track}** by **{artist}** — "
                f"**{source_name}** has been playing it ({plays}× this week)"
            )
            await mm.post_message(  # type: ignore[attr-defined]
                channel_id=self._settings.mattermost_channel, message=msg
            )
        except Exception as e:
            logger.debug("scrobble announce failed: %s", e)
