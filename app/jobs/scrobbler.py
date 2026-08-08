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
            # Tally plays within the lookback window per (artist, track), counting
            # DISTINCT multi-scrobbler playHashes — NOT raw feed appearances.
            #
            # Why playHash: multi-scrobbler's Apple "recently played" feed re-dumps
            # your history on every restart, stamping each old play with a fresh
            # timestamp. Counting appearances (or distinct playDates) inflated a
            # single listen into "4 plays" across 4 restarts and flooded the
            # library. But every one of those dumps carries the SAME playHash
            # (hash of track+artist+context, not the fake timestamp), so counting
            # distinct playHashes collapses restart-dumps to the true play count.
            # A genuine repeat-listen produces a distinct playHash.
            counts: dict[tuple[str, str], dict] = {}
            for entry in plays:
                # Each entry: { play: { data: {track, artists:[{name}], ...} },
                #               playedAt, playHash, uid }
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
                # Dedup key for a distinct play: prefer playHash, fall back to uid.
                play_key = entry.get("playHash") or entry.get("uid") or pd
                key = (_norm(artist), _norm(track))
                slot = counts.setdefault(
                    key, {"artist": artist, "track": track, "hashes": set()}
                )
                slot["hashes"].add(play_key)

            for (nart, ntrk), info in counts.items():
                n = len(info["hashes"])
                if n < threshold:
                    continue
                added = await self._maybe_enqueue(
                    source_name=source_name,
                    artist=info["artist"],
                    track=info["track"],
                    plays=n,
                    norm_key=f"{nart}||{ntrk}",
                )
                if added:
                    enqueued += 1

        if enqueued:
            logger.info("Scrobble watcher enqueued %d new track(s)", enqueued)
        return enqueued

    # --- Health / token-expiry monitoring -----------------------------------

    async def check_health(self) -> int:
        """Detect broken sources (e.g. expired Apple media-user-token) and DM
        the affected user a personal re-link link in Mattermost.

        Returns the number of alerts sent this pass.
        """
        session = await self._get_session()
        url = f"{self._settings.multiscrobbler_url}/api/status"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json()
        except Exception as exc:
            logger.debug("health: multi-scrobbler status unreachable: %s", exc)
            return 0

        alerts = 0
        for s in data.get("sources", []):
            name = s.get("name")
            stype = s.get("type")
            status = (s.get("status") or "").lower()
            authed = s.get("authed")
            # Healthy sources report status "Polling"/"Running". A broken source
            # has lost auth (authed False) or dropped out of polling. NOTE:
            # `hasAuthInteraction` only means the source TYPE supports an auth
            # step (Spotify has it and is perfectly healthy) — it is NOT a
            # broken signal, so we don't use it here.
            healthy_states = ("polling", "running", "getting ready", "idle", "")
            broken = authed is False or (status not in healthy_states)
            if not name or not broken:
                continue
            if await self._recently_alerted(name):
                continue
            await self._alert_user_relink(name, stype)
            await self._mark_alerted(name)
            alerts += 1
        return alerts

    async def _recently_alerted(self, source_name: str) -> bool:
        """Rate-limit: was this source alerted within the cooldown window?"""
        from app.jobs.scrobble_state import last_alert_at

        ts = await last_alert_at(source_name)
        if ts is None:
            return False
        cooldown = timedelta(hours=self._settings.scrobble_health_alert_hours)
        return (datetime.now(timezone.utc) - ts) < cooldown

    async def _mark_alerted(self, source_name: str) -> None:
        from app.jobs.scrobble_state import record_alert

        await record_alert(source_name)

    async def _alert_user_relink(self, source_name: str, stype: str) -> None:
        """DM the user (by Mattermost username == source name) a re-link link."""
        try:
            mm = getattr(self.pipeline, "mattermost", None)
            if mm is None:
                logger.warning("health: no Mattermost client to alert %s", source_name)
                return
            # Only Apple currently needs manual re-link; Spotify auto-refreshes.
            service = "Apple Music" if stype == "applemusic" else stype
            # Clean URL — the page is public and prefills name/service; no key.
            svc_param = "applemusic" if stype == "applemusic" else stype
            relink = (
                f"{self._settings.relink_base_url}/"
                f"?name={source_name}&service={svc_param}"
            )
            msg = (
                f"⚠️ Your **{service}** connection to Slaptastic stopped working "
                f"(the login token expired — this happens periodically).\n\n"
                f"**One click to fix it:** [Re-link {service}]({relink})\n\n"
                f"Sign in when prompted and your listening will reconnect automatically."
            )
            # Prefer a direct message to the user; fall back to channel mention.
            sent = await self._dm_user(mm, source_name, msg)
            if not sent and self._settings.mattermost_channel:
                await mm.post_message(  # type: ignore[attr-defined]
                    channel_id=self._settings.mattermost_channel,
                    message=f"@{source_name} {msg}",
                )
            logger.info("health: alerted %s to re-link %s", source_name, service)
        except Exception as e:
            logger.warning("health: failed to alert %s: %s", source_name, e)

    async def _dm_user(self, mm, username: str, message: str) -> bool:
        """Open a DM channel with the user and post the message. Best-effort."""
        settings = self._settings
        session = await self._get_session()
        base = settings.mattermost_url
        tok = settings.mattermost_token
        if not base or not tok:
            return False
        headers = {"Authorization": f"Bearer {tok}"}
        try:
            # Resolve target user id + the bot's own id.
            async with session.get(
                f"{base}/api/v4/users/username/{username}", headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return False
                target_id = (await r.json()).get("id")
            async with session.get(
                f"{base}/api/v4/users/me", headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return False
                bot_id = (await r.json()).get("id")
            if not target_id or not bot_id:
                return False
            # Create (or fetch) the DM channel between bot and user.
            async with session.post(
                f"{base}/api/v4/channels/direct", headers=headers,
                json=[bot_id, target_id],
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status not in (200, 201):
                    return False
                dm_channel_id = (await r.json()).get("id")
            await mm.post_message(channel_id=dm_channel_id, message=message)  # type: ignore[attr-defined]
            return True
        except Exception as e:
            logger.debug("DM to %s failed: %s", username, e)
            return False

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
