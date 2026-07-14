"""Dashboard API endpoints for the Slapshare music leaderboard.

Public endpoints (no auth required) that provide aggregated statistics
for the music leaderboard dashboard.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.models.job import Job, JobStatus

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# User ID to display name mapping
USER_DISPLAY_NAMES: dict[str, str] = {
    "e3pqz61dgjyq9pjcay9zk18cbh": "moiz",
    "a7a5hiwbe3n57koxmxbhu74jqh": "themoosecompany",
    "arkxtkrs8fbwbyhpx9tgcaujxh": "shahraiz",
    "srrgmm688pds7fiqndeweew6zr": "zubair221b",
    "dwgmwjsuufnk7g5hm9diwb6hyy": "nooramin40",
    "pwdagarckfdijypad9of9ymprh": "deception",
    "faujhgzs73do7j3tuide4jtsxc": "guest",
}

# Avatar colors for each user
USER_COLORS: dict[str, str] = {
    "moiz": "#8b5cf6",
    "themoosecompany": "#06b6d4",
    "shahraiz": "#f43f5e",
    "zubair221b": "#10b981",
    "nooramin40": "#f59e0b",
    "deception": "#ec4899",
    "guest": "#6b7280",
}


def _get_display_name(user_id: str | None) -> str:
    """Convert a Mattermost user ID to a display name."""
    if user_id is None:
        return "unknown"
    return USER_DISPLAY_NAMES.get(user_id, user_id[:8])


# --- Response Models ---


class StatsResponse(BaseModel):
    total_songs: int
    total_contributors: int
    this_week_additions: int
    top_artist: str | None
    total_artists: int
    most_active_day: str | None
    peak_hour: int | None
    longest_streak_user: str | None
    longest_streak_days: int


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    song_count: int
    color: str
    latest_addition: str | None


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


class RecentEntry(BaseModel):
    title: str | None
    artist: str | None
    album: str | None
    username: str
    color: str
    created_at: str
    source_platform: str
    url: str


class RecentResponse(BaseModel):
    items: list[RecentEntry]


class GenreEntry(BaseModel):
    name: str
    count: int
    percentage: float


class GenresResponse(BaseModel):
    genres: list[GenreEntry]


class TimelineEntry(BaseModel):
    date: str
    count: int


class TimelineResponse(BaseModel):
    entries: list[TimelineEntry]


class ArtistEntry(BaseModel):
    name: str
    count: int
    latest_album: str | None


class ArtistsResponse(BaseModel):
    artists: list[ArtistEntry]


class UserProfileResponse(BaseModel):
    username: str
    color: str
    total_songs: int
    rank: int
    favorite_artist: str | None
    favorite_platform: str | None
    first_submission: str | None
    latest_submission: str | None
    submissions: list[RecentEntry]


# --- Endpoints ---


@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: DbSession) -> StatsResponse:
    """Get overview statistics for the dashboard."""
    # Total completed songs
    total_result = await db.execute(
        select(func.count(Job.id)).where(Job.status == JobStatus.COMPLETE)
    )
    total_songs = total_result.scalar_one()

    # Total contributors
    contributors_result = await db.execute(
        select(func.count(func.distinct(Job.requester_user_id))).where(
            Job.status == JobStatus.COMPLETE
        )
    )
    total_contributors = contributors_result.scalar_one()

    # This week's additions
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_result = await db.execute(
        select(func.count(Job.id)).where(
            Job.status == JobStatus.COMPLETE,
            Job.created_at >= one_week_ago,
        )
    )
    this_week_additions = week_result.scalar_one()

    # Top artist
    top_artist_result = await db.execute(
        select(Job.artist, func.count(Job.id).label("cnt"))
        .where(Job.status == JobStatus.COMPLETE, Job.artist.isnot(None))
        .group_by(Job.artist)
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    top_artist_row = top_artist_result.first()
    top_artist = top_artist_row[0] if top_artist_row else None

    # Total unique artists
    artists_result = await db.execute(
        select(func.count(func.distinct(Job.artist))).where(
            Job.status == JobStatus.COMPLETE, Job.artist.isnot(None)
        )
    )
    total_artists = artists_result.scalar_one()

    # Most active day of week (0=Monday, 6=Sunday)
    # SQLite uses strftime, so we handle it directly
    day_result = await db.execute(
        select(
            func.strftime("%w", Job.created_at).label("dow"),
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE)
        .group_by("dow")
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    day_row = day_result.first()
    day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    most_active_day = day_names[int(day_row[0])] if day_row else None

    # Peak hour
    hour_result = await db.execute(
        select(
            func.strftime("%H", Job.created_at).label("hour"),
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE)
        .group_by("hour")
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    hour_row = hour_result.first()
    peak_hour = int(hour_row[0]) if hour_row else None

    # Longest streak (consecutive days with submissions per user)
    longest_streak_user = None
    longest_streak_days = 0

    # Get all users and their submission dates
    streak_result = await db.execute(
        select(Job.requester_user_id, func.date(Job.created_at).label("sub_date"))
        .where(Job.status == JobStatus.COMPLETE)
        .distinct()
        .order_by(Job.requester_user_id, "sub_date")
    )
    streak_rows = streak_result.all()

    # Calculate streaks per user
    current_user = None
    current_streak = 0
    prev_date = None
    for row in streak_rows:
        user_id, date_str = row[0], row[1]
        if user_id != current_user:
            current_user = user_id
            current_streak = 1
            prev_date = date_str
        else:
            # Check if consecutive
            if prev_date and date_str:
                try:
                    d1 = datetime.strptime(str(prev_date), "%Y-%m-%d")
                    d2 = datetime.strptime(str(date_str), "%Y-%m-%d")
                    if (d2 - d1).days == 1:
                        current_streak += 1
                    else:
                        current_streak = 1
                except (ValueError, TypeError):
                    current_streak = 1
            prev_date = date_str

        if current_streak > longest_streak_days:
            longest_streak_days = current_streak
            longest_streak_user = _get_display_name(user_id)

    return StatsResponse(
        total_songs=total_songs,
        total_contributors=total_contributors,
        this_week_additions=this_week_additions,
        top_artist=top_artist,
        total_artists=total_artists,
        most_active_day=most_active_day,
        peak_hour=peak_hour,
        longest_streak_user=longest_streak_user,
        longest_streak_days=longest_streak_days,
    )


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(db: DbSession) -> LeaderboardResponse:
    """Get user rankings by song count."""
    result = await db.execute(
        select(
            Job.requester_user_id,
            func.count(Job.id).label("cnt"),
            func.max(Job.created_at).label("latest"),
        )
        .where(Job.status == JobStatus.COMPLETE)
        .group_by(Job.requester_user_id)
        .order_by(func.count(Job.id).desc())
    )
    rows = result.all()

    entries = []
    for rank, row in enumerate(rows, 1):
        username = _get_display_name(row[0])
        entries.append(
            LeaderboardEntry(
                rank=rank,
                username=username,
                song_count=row[1],
                color=USER_COLORS.get(username, "#6b7280"),
                latest_addition=row[2].isoformat() if row[2] else None,
            )
        )

    return LeaderboardResponse(entries=entries)


@router.get("/recent", response_model=RecentResponse)
async def get_recent(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=50),
) -> RecentResponse:
    """Get the most recent song additions."""
    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.COMPLETE)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    jobs = result.scalars().all()

    items = []
    for job in jobs:
        username = _get_display_name(job.requester_user_id)
        items.append(
            RecentEntry(
                title=job.title,
                artist=job.artist,
                album=job.album,
                username=username,
                color=USER_COLORS.get(username, "#6b7280"),
                created_at=job.created_at.isoformat(),
                source_platform=job.source_platform.value if job.source_platform else "unknown",
                url=job.url,
            )
        )

    return RecentResponse(items=items)


@router.get("/genres", response_model=GenresResponse)
async def get_genres(db: DbSession) -> GenresResponse:
    """Get genre/source platform breakdown.

    Since we don't have explicit genre tags, we use source platform
    as a proxy and also try to infer genre from artist patterns.
    """
    # Use source platform as a category breakdown
    result = await db.execute(
        select(
            Job.source_platform,
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE)
        .group_by(Job.source_platform)
        .order_by(func.count(Job.id).desc())
    )
    rows = result.all()

    total = sum(row[1] for row in rows)
    genres = []
    platform_labels = {
        "spotify": "Spotify Finds",
        "apple_music": "Apple Music",
        "youtube": "YouTube Discoveries",
        "unknown": "Other Sources",
    }
    for row in rows:
        platform_name = row[0].value if hasattr(row[0], "value") else str(row[0])
        label = platform_labels.get(platform_name, platform_name)
        genres.append(
            GenreEntry(
                name=label,
                count=row[1],
                percentage=round((row[1] / total) * 100, 1) if total > 0 else 0,
            )
        )

    return GenresResponse(genres=genres)


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(db: DbSession) -> TimelineResponse:
    """Get submission counts over time (by date)."""
    result = await db.execute(
        select(
            func.date(Job.created_at).label("sub_date"),
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE)
        .group_by("sub_date")
        .order_by("sub_date")
    )
    rows = result.all()

    entries = [
        TimelineEntry(date=str(row[0]), count=row[1])
        for row in rows
    ]

    return TimelineResponse(entries=entries)


@router.get("/artists", response_model=ArtistsResponse)
async def get_artists(
    db: DbSession,
    limit: int = Query(default=15, ge=1, le=50),
) -> ArtistsResponse:
    """Get the most submitted artists."""
    result = await db.execute(
        select(
            Job.artist,
            func.count(Job.id).label("cnt"),
            func.max(Job.album).label("latest_album"),
        )
        .where(Job.status == JobStatus.COMPLETE, Job.artist.isnot(None))
        .group_by(Job.artist)
        .order_by(func.count(Job.id).desc())
        .limit(limit)
    )
    rows = result.all()

    artists = [
        ArtistEntry(
            name=row[0],
            count=row[1],
            latest_album=row[2],
        )
        for row in rows
    ]

    return ArtistsResponse(artists=artists)


@router.get("/user/{username}", response_model=UserProfileResponse)
async def get_user_profile(username: str, db: DbSession) -> UserProfileResponse:
    """Get detailed profile for a specific user."""
    # Reverse lookup: username -> user_id
    user_id = None
    for uid, name in USER_DISPLAY_NAMES.items():
        if name == username:
            user_id = uid
            break

    if user_id is None:
        # Return empty profile
        return UserProfileResponse(
            username=username,
            color="#6b7280",
            total_songs=0,
            rank=0,
            favorite_artist=None,
            favorite_platform=None,
            first_submission=None,
            latest_submission=None,
            submissions=[],
        )

    # Get all completed jobs for this user
    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id == user_id)
        .order_by(Job.created_at.desc())
    )
    jobs = result.scalars().all()

    total_songs = len(jobs)

    # Calculate rank
    rank_result = await db.execute(
        select(Job.requester_user_id, func.count(Job.id).label("cnt"))
        .where(Job.status == JobStatus.COMPLETE)
        .group_by(Job.requester_user_id)
        .order_by(func.count(Job.id).desc())
    )
    rank_rows = rank_result.all()
    rank = 0
    for i, row in enumerate(rank_rows, 1):
        if row[0] == user_id:
            rank = i
            break

    # Favorite artist
    fav_artist_result = await db.execute(
        select(Job.artist, func.count(Job.id).label("cnt"))
        .where(
            Job.status == JobStatus.COMPLETE,
            Job.requester_user_id == user_id,
            Job.artist.isnot(None),
        )
        .group_by(Job.artist)
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    fav_artist_row = fav_artist_result.first()
    favorite_artist = fav_artist_row[0] if fav_artist_row else None

    # Favorite platform
    fav_platform_result = await db.execute(
        select(Job.source_platform, func.count(Job.id).label("cnt"))
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id == user_id)
        .group_by(Job.source_platform)
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    fav_platform_row = fav_platform_result.first()
    favorite_platform = (
        fav_platform_row[0].value if fav_platform_row and fav_platform_row[0] else None
    )

    submissions = []
    for job in jobs:
        submissions.append(
            RecentEntry(
                title=job.title,
                artist=job.artist,
                album=job.album,
                username=username,
                color=USER_COLORS.get(username, "#6b7280"),
                created_at=job.created_at.isoformat(),
                source_platform=job.source_platform.value if job.source_platform else "unknown",
                url=job.url,
            )
        )

    return UserProfileResponse(
        username=username,
        color=USER_COLORS.get(username, "#6b7280"),
        total_songs=total_songs,
        rank=rank,
        favorite_artist=favorite_artist,
        favorite_platform=favorite_platform,
        first_submission=jobs[-1].created_at.isoformat() if jobs else None,
        latest_submission=jobs[0].created_at.isoformat() if jobs else None,
        submissions=submissions,
    )
