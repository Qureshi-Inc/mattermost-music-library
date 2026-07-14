"""Listening activity API — play events, now listening, stats for SlapPlayer."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.models.play_event import PlayEvent

router = APIRouter(prefix="/listening", tags=["listening"])


# --- Request/Response Models ---


class PlayEventRequest(BaseModel):
    username: str
    track_id: str
    title: str
    artist: str
    album: str | None = None
    duration_seconds: int = 0
    listened_seconds: int = 0
    completed: bool = False
    hour_of_day: int = 0
    skipped: bool = False
    thumbs: int = 0


class PlayEventResponse(BaseModel):
    id: str
    username: str
    track_id: str
    title: str
    artist: str
    album: str | None
    listened_seconds: int
    completed: bool
    skipped: bool
    created_at: str


class NowListeningEntry(BaseModel):
    username: str
    track_id: str
    title: str
    artist: str
    album: str | None
    listened_seconds: int
    started_at: str
    is_recent: bool


class NowListeningResponse(BaseModel):
    listeners: list[NowListeningEntry]


class ListeningStatsEntry(BaseModel):
    track_id: str
    title: str
    artist: str
    album: str | None
    play_count: int
    total_listen_seconds: int
    unique_listeners: int
    skip_rate: float
    avg_completion: float


class ListeningStatsResponse(BaseModel):
    most_played: list[ListeningStatsEntry]
    total_plays: int
    total_listen_hours: float
    unique_tracks_played: int
    unique_listeners: int


class UserListeningProfile(BaseModel):
    username: str
    total_plays: int
    total_listen_hours: float
    top_tracks: list[ListeningStatsEntry]
    top_artists: list[dict]
    peak_hour: int | None
    avg_session_length: float
    completion_rate: float
    discovery_count: int


class ActivityFeedEntry(BaseModel):
    username: str
    title: str
    artist: str
    album: str | None
    track_id: str
    listened_seconds: int
    completed: bool
    skipped: bool
    timestamp: str


class ActivityFeedResponse(BaseModel):
    activity: list[ActivityFeedEntry]


class EngagementSnapshot(BaseModel):
    track_id: str
    title: str
    artist: str
    play_count: int
    total_listen_seconds: int
    skip_count: int
    thumb_ups: int
    thumb_downs: int
    unique_listeners: int
    avg_listen_pct: float
    peak_hour: int | None
    listeners: list[str]


class FullEngagementResponse(BaseModel):
    tracks: list[EngagementSnapshot]
    user_profiles: list[UserListeningProfile]
    generated_at: str


# --- Endpoints ---


@router.post("/play", response_model=PlayEventResponse)
async def record_play(body: PlayEventRequest, db: DbSession) -> PlayEventResponse:
    """Record a play event (called when user listens 30+ seconds)."""
    event = PlayEvent(
        username=body.username,
        track_id=body.track_id,
        title=body.title,
        artist=body.artist,
        album=body.album,
        duration_seconds=body.duration_seconds,
        listened_seconds=body.listened_seconds,
        completed=body.completed,
        hour_of_day=body.hour_of_day,
        skipped=body.skipped,
        thumbs=body.thumbs,
    )
    db.add(event)
    await db.flush()

    return PlayEventResponse(
        id=str(event.id),
        username=event.username,
        track_id=event.track_id,
        title=event.title,
        artist=event.artist,
        album=event.album,
        listened_seconds=event.listened_seconds,
        completed=event.completed,
        skipped=event.skipped,
        created_at=event.created_at.isoformat(),
    )


@router.post("/skip")
async def record_skip(body: PlayEventRequest, db: DbSession) -> dict:
    """Record a skip event (listened < 15 seconds then changed)."""
    event = PlayEvent(
        username=body.username,
        track_id=body.track_id,
        title=body.title,
        artist=body.artist,
        album=body.album,
        duration_seconds=body.duration_seconds,
        listened_seconds=body.listened_seconds,
        completed=False,
        hour_of_day=body.hour_of_day,
        skipped=True,
        thumbs=body.thumbs,
    )
    db.add(event)
    await db.flush()
    return {"ok": True}


@router.get("/now", response_model=NowListeningResponse)
async def get_now_listening(db: DbSession) -> NowListeningResponse:
    """Get what people are currently/recently listening to (last 10 minutes)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    result = await db.execute(
        select(PlayEvent)
        .where(PlayEvent.created_at >= cutoff, PlayEvent.skipped == False)
        .order_by(PlayEvent.created_at.desc())
    )
    events = result.scalars().all()

    seen_users = set()
    listeners = []
    for ev in events:
        if ev.username in seen_users:
            continue
        seen_users.add(ev.username)
        age = (datetime.now(timezone.utc) - ev.created_at).total_seconds()
        listeners.append(NowListeningEntry(
            username=ev.username,
            track_id=ev.track_id,
            title=ev.title,
            artist=ev.artist,
            album=ev.album,
            listened_seconds=ev.listened_seconds,
            started_at=ev.created_at.isoformat(),
            is_recent=age < 300,
        ))

    return NowListeningResponse(listeners=listeners)


@router.get("/feed", response_model=ActivityFeedResponse)
async def get_activity_feed(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    hours: int = Query(default=24, ge=1, le=168),
) -> ActivityFeedResponse:
    """Get the recent listening activity feed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(PlayEvent)
        .where(PlayEvent.created_at >= cutoff)
        .order_by(PlayEvent.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()

    activity = [
        ActivityFeedEntry(
            username=ev.username,
            title=ev.title,
            artist=ev.artist,
            album=ev.album,
            track_id=ev.track_id,
            listened_seconds=ev.listened_seconds,
            completed=ev.completed,
            skipped=ev.skipped,
            timestamp=ev.created_at.isoformat(),
        )
        for ev in events
    ]

    return ActivityFeedResponse(activity=activity)


@router.get("/stats", response_model=ListeningStatsResponse)
async def get_listening_stats(
    db: DbSession,
    period: str = Query(default="all", regex="^(week|month|year|all)$"),
) -> ListeningStatsResponse:
    """Get aggregate listening stats."""
    filters = [PlayEvent.skipped == False]
    if period == "week":
        filters.append(PlayEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=7))
    elif period == "month":
        filters.append(PlayEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=30))
    elif period == "year":
        filters.append(PlayEvent.created_at >= datetime.now(timezone.utc) - timedelta(days=365))

    result = await db.execute(
        select(
            PlayEvent.track_id,
            PlayEvent.title,
            PlayEvent.artist,
            PlayEvent.album,
            func.count(PlayEvent.id).label("play_count"),
            func.sum(PlayEvent.listened_seconds).label("total_listen"),
            func.count(func.distinct(PlayEvent.username)).label("unique_listeners"),
        )
        .where(*filters)
        .group_by(PlayEvent.track_id, PlayEvent.title, PlayEvent.artist, PlayEvent.album)
        .order_by(func.count(PlayEvent.id).desc())
        .limit(20)
    )
    rows = result.all()

    # Get skip data
    skip_result = await db.execute(
        select(
            PlayEvent.track_id,
            func.count(PlayEvent.id).label("total"),
            func.sum(func.cast(PlayEvent.skipped, Integer)).label("skips"),
            func.avg(
                func.cast(PlayEvent.listened_seconds, Float) /
                func.nullif(func.cast(PlayEvent.duration_seconds, Float), 0)
            ).label("avg_completion"),
        )
        .group_by(PlayEvent.track_id)
    )
    skip_map = {r[0]: (r[2] or 0, r[1] or 1, r[3] or 0) for r in skip_result.all()}

    most_played = []
    for row in rows:
        tid = row[0]
        skips, total, avg_comp = skip_map.get(tid, (0, 1, 0))
        most_played.append(ListeningStatsEntry(
            track_id=tid,
            title=row[1],
            artist=row[2],
            album=row[3],
            play_count=row[4],
            total_listen_seconds=row[5] or 0,
            unique_listeners=row[6],
            skip_rate=round(skips / total, 2) if total > 0 else 0,
            avg_completion=round(float(avg_comp), 2),
        ))

    # Totals
    totals = await db.execute(
        select(
            func.count(PlayEvent.id),
            func.sum(PlayEvent.listened_seconds),
            func.count(func.distinct(PlayEvent.track_id)),
            func.count(func.distinct(PlayEvent.username)),
        ).where(*filters)
    )
    t = totals.first()

    return ListeningStatsResponse(
        most_played=most_played,
        total_plays=t[0] or 0,
        total_listen_hours=round((t[1] or 0) / 3600, 1),
        unique_tracks_played=t[2] or 0,
        unique_listeners=t[3] or 0,
    )


@router.get("/user/{username}", response_model=UserListeningProfile)
async def get_user_listening(username: str, db: DbSession) -> UserListeningProfile:
    """Get a user's listening profile with behavioral patterns."""
    result = await db.execute(
        select(PlayEvent)
        .where(PlayEvent.username == username, PlayEvent.skipped == False)
        .order_by(PlayEvent.created_at.desc())
    )
    events = result.scalars().all()

    if not events:
        return UserListeningProfile(
            username=username, total_plays=0, total_listen_hours=0,
            top_tracks=[], top_artists=[], peak_hour=None,
            avg_session_length=0, completion_rate=0, discovery_count=0,
        )

    total_listen = sum(e.listened_seconds for e in events)
    total_duration = sum(e.duration_seconds for e in events if e.duration_seconds > 0)

    # Top tracks
    track_counts = defaultdict(lambda: {"count": 0, "listen": 0, "title": "", "artist": "", "album": None, "track_id": ""})
    for e in events:
        t = track_counts[e.track_id]
        t["count"] += 1
        t["listen"] += e.listened_seconds
        t["title"] = e.title
        t["artist"] = e.artist
        t["album"] = e.album
        t["track_id"] = e.track_id

    top_tracks = sorted(track_counts.values(), key=lambda x: x["count"], reverse=True)[:10]
    top_tracks_out = [
        ListeningStatsEntry(
            track_id=t["track_id"], title=t["title"], artist=t["artist"], album=t["album"],
            play_count=t["count"], total_listen_seconds=t["listen"],
            unique_listeners=1, skip_rate=0, avg_completion=0,
        )
        for t in top_tracks
    ]

    # Top artists
    artist_counts = defaultdict(int)
    for e in events:
        artist_counts[e.artist] += 1
    top_artists = [{"name": k, "plays": v} for k, v in sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)[:10]]

    # Peak hour
    hour_counts = defaultdict(int)
    for e in events:
        hour_counts[e.hour_of_day] += 1
    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None

    # Discovery — unique tracks only played once
    discovery_count = sum(1 for t in track_counts.values() if t["count"] == 1)

    return UserListeningProfile(
        username=username,
        total_plays=len(events),
        total_listen_hours=round(total_listen / 3600, 1),
        top_tracks=top_tracks_out,
        top_artists=top_artists,
        peak_hour=peak_hour,
        avg_session_length=round(total_listen / max(len(events), 1)),
        completion_rate=round(total_listen / max(total_duration, 1), 2),
        discovery_count=discovery_count,
    )


@router.get("/engagement", response_model=FullEngagementResponse)
async def get_full_engagement(db: DbSession) -> FullEngagementResponse:
    """Get full engagement data for AI playlist generation — all signals in one call."""
    result = await db.execute(
        select(PlayEvent).order_by(PlayEvent.created_at.desc()).limit(5000)
    )
    events = result.scalars().all()

    # Build per-track engagement
    track_data = defaultdict(lambda: {
        "title": "", "artist": "", "plays": 0, "listen": 0,
        "skips": 0, "thumb_ups": 0, "thumb_downs": 0,
        "listeners": set(), "hours": [], "durations": [],
    })
    for ev in events:
        t = track_data[ev.track_id]
        t["title"] = ev.title
        t["artist"] = ev.artist
        if ev.skipped:
            t["skips"] += 1
        else:
            t["plays"] += 1
            t["listen"] += ev.listened_seconds
        if ev.thumbs > 0:
            t["thumb_ups"] += 1
        elif ev.thumbs < 0:
            t["thumb_downs"] += 1
        t["listeners"].add(ev.username)
        t["hours"].append(ev.hour_of_day)
        if ev.duration_seconds > 0:
            t["durations"].append(ev.listened_seconds / ev.duration_seconds)

    tracks = []
    for tid, d in sorted(track_data.items(), key=lambda x: x[1]["plays"], reverse=True)[:100]:
        hour_counts = defaultdict(int)
        for h in d["hours"]:
            hour_counts[h] += 1
        peak = max(hour_counts, key=hour_counts.get) if hour_counts else None

        tracks.append(EngagementSnapshot(
            track_id=tid,
            title=d["title"],
            artist=d["artist"],
            play_count=d["plays"],
            total_listen_seconds=d["listen"],
            skip_count=d["skips"],
            thumb_ups=d["thumb_ups"],
            thumb_downs=d["thumb_downs"],
            unique_listeners=len(d["listeners"]),
            avg_listen_pct=round(sum(d["durations"]) / max(len(d["durations"]), 1), 2),
            peak_hour=peak,
            listeners=list(d["listeners"]),
        ))

    # Build user profiles (lightweight)
    user_events = defaultdict(list)
    for ev in events:
        if not ev.skipped:
            user_events[ev.username].append(ev)

    user_profiles = []
    for username, evs in user_events.items():
        total_listen = sum(e.listened_seconds for e in evs)
        artist_counts = defaultdict(int)
        for e in evs:
            artist_counts[e.artist] += 1
        top_artists = [{"name": k, "plays": v} for k, v in sorted(artist_counts.items(), key=lambda x: x[1], reverse=True)[:5]]

        hour_counts = defaultdict(int)
        for e in evs:
            hour_counts[e.hour_of_day] += 1
        peak = max(hour_counts, key=hour_counts.get) if hour_counts else None

        user_profiles.append(UserListeningProfile(
            username=username,
            total_plays=len(evs),
            total_listen_hours=round(total_listen / 3600, 1),
            top_tracks=[],
            top_artists=top_artists,
            peak_hour=peak,
            avg_session_length=round(total_listen / max(len(evs), 1)),
            completion_rate=0,
            discovery_count=0,
        ))

    return FullEngagementResponse(
        tracks=tracks,
        user_profiles=user_profiles,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
