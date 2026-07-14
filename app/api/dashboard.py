"""Dashboard API endpoints for the Slapshare music leaderboard.

Public endpoints (no auth required) that provide aggregated statistics
for the music leaderboard dashboard.
"""

from collections import defaultdict
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
    "dwgmwjsuufnk7g5hm9diwb6hyy": "zubair221b",
    "pwdagarckfdijypad9of9ymprh": "nooramin40",
    "faujhgzs73do7j3tuide4jtsxc": "deception",
}

# Bot user IDs to exclude from leaderboard
BOT_USER_IDS = {"srrgmm688pds7fiqndeweew6zr"}  # slapper bot

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
        select(func.count(Job.id)).where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
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


# --- Additional Response Models for Premium Dashboard ---


class HeatmapCell(BaseModel):
    day: int  # 0=Sun, 6=Sat
    hour: int  # 0-23
    count: int


class HeatmapResponse(BaseModel):
    cells: list[HeatmapCell]
    max_count: int


class AchievementEntry(BaseModel):
    id: str
    name: str
    emoji: str
    description: str
    unlocked: bool
    unlocked_by: list[str]


class AchievementsResponse(BaseModel):
    achievements: list[AchievementEntry]


class HipsterEntry(BaseModel):
    username: str
    color: str
    unique_artists: int
    hipster_score: float  # lower = more hipster


class HipsterResponse(BaseModel):
    entries: list[HipsterEntry]


class StreakEntry(BaseModel):
    username: str
    color: str
    current_streak: int
    longest_streak: int
    is_active: bool


class StreaksResponse(BaseModel):
    entries: list[StreakEntry]


class PersonalityCard(BaseModel):
    username: str
    color: str
    personality: str
    description: str
    dominant_platform: str
    song_count: int


class PersonalitiesResponse(BaseModel):
    cards: list[PersonalityCard]


class HeadToHeadResponse(BaseModel):
    user1: str
    user2: str
    user1_color: str
    user2_color: str
    user1_songs: int
    user2_songs: int
    user1_artists: int
    user2_artists: int
    user1_platforms: dict[str, int]
    user2_platforms: dict[str, int]
    shared_artists: list[str]
    user1_unique_artists: list[str]
    user2_unique_artists: list[str]


class HallOfFameEntry(BaseModel):
    title: str
    description: str
    value: str
    emoji: str


class HallOfFameResponse(BaseModel):
    entries: list[HallOfFameEntry]


# --- Additional Endpoints ---


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_heatmap(db: DbSession) -> HeatmapResponse:
    """Get activity heatmap data (hour x day of week)."""
    result = await db.execute(
        select(
            func.strftime("%w", Job.created_at).label("dow"),
            func.strftime("%H", Job.created_at).label("hour"),
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
        .group_by("dow", "hour")
    )
    rows = result.all()

    cells = []
    max_count = 0
    for row in rows:
        count = row[2]
        if count > max_count:
            max_count = count
        cells.append(HeatmapCell(day=int(row[0]), hour=int(row[1]), count=count))

    return HeatmapResponse(cells=cells, max_count=max_count)


@router.get("/achievements", response_model=AchievementsResponse)
async def get_achievements(db: DbSession) -> AchievementsResponse:
    """Get achievement badges with unlock status."""
    # Get all completed jobs
    result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
        .order_by(Job.created_at.asc())
    )
    jobs = result.scalars().all()

    # Build per-user data
    user_songs: dict[str, list] = defaultdict(list)
    for job in jobs:
        username = _get_display_name(job.requester_user_id)
        user_songs[username].append(job)

    achievements = []

    # First Blood - first song added
    first_blood_users = []
    if jobs:
        first_job = jobs[0]
        first_blood_users = [_get_display_name(first_job.requester_user_id)]
    achievements.append(AchievementEntry(
        id="first_blood", name="First Blood", emoji="\U0001fa78",
        description="Added the very first song to Slapshare",
        unlocked=len(first_blood_users) > 0, unlocked_by=first_blood_users,
    ))

    # Top Slapper - most songs
    top_slapper_users = []
    if user_songs:
        max_songs = max(len(songs) for songs in user_songs.values())
        top_slapper_users = [u for u, s in user_songs.items() if len(s) == max_songs]
    achievements.append(AchievementEntry(
        id="top_slapper", name="Top Slapper", emoji="\U0001f451",
        description="Has the most songs in the library",
        unlocked=len(top_slapper_users) > 0, unlocked_by=top_slapper_users,
    ))

    # Night Owl - added after midnight (00:00-05:00)
    night_owl_users = []
    for username, songs in user_songs.items():
        for job in songs:
            if job.created_at and job.created_at.hour < 5:
                night_owl_users.append(username)
                break
    achievements.append(AchievementEntry(
        id="night_owl", name="Night Owl", emoji="\U0001f989",
        description="Added a song after midnight",
        unlocked=len(night_owl_users) > 0, unlocked_by=night_owl_users,
    ))

    # Streak Master - 7+ day streak
    streak_master_users = []
    for username, songs in user_songs.items():
        dates = sorted(set(job.created_at.date() for job in songs if job.created_at))
        max_streak = 1
        current = 1
        for i in range(1, len(dates)):
            if (dates[i] - dates[i - 1]).days == 1:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 1
        if max_streak >= 7:
            streak_master_users.append(username)
    achievements.append(AchievementEntry(
        id="streak_master", name="Streak Master", emoji="\U0001f525",
        description="Maintained a 7+ day submission streak",
        unlocked=len(streak_master_users) > 0, unlocked_by=streak_master_users,
    ))

    # Genre Bender - 5+ platforms (using source_platform as proxy)
    genre_bender_users = []
    for username, songs in user_songs.items():
        artists = set(job.artist for job in songs if job.artist)
        if len(artists) >= 5:
            genre_bender_users.append(username)
    achievements.append(AchievementEntry(
        id="genre_bender", name="Genre Bender", emoji="\U0001f3ad",
        description="Added songs from 5+ different artists",
        unlocked=len(genre_bender_users) > 0, unlocked_by=genre_bender_users,
    ))

    # Century - 100 songs
    century_users = [u for u, s in user_songs.items() if len(s) >= 100]
    achievements.append(AchievementEntry(
        id="century", name="Century", emoji="\U0001f4af",
        description="Added 100 songs to the library",
        unlocked=len(century_users) > 0, unlocked_by=century_users,
    ))

    # Trailblazer - introduced 5+ unique artists nobody else submitted
    trailblazer_users = []
    all_user_artists: dict[str, set] = {}
    for username, songs in user_songs.items():
        all_user_artists[username] = set(job.artist for job in songs if job.artist)
    for username, artists in all_user_artists.items():
        unique_count = 0
        for artist in artists:
            other_users_have = any(
                artist in other_artists
                for other_user, other_artists in all_user_artists.items()
                if other_user != username
            )
            if not other_users_have:
                unique_count += 1
        if unique_count >= 5:
            trailblazer_users.append(username)
    achievements.append(AchievementEntry(
        id="trailblazer", name="Trailblazer", emoji="\U0001f3d4️",
        description="Introduced 5+ artists nobody else has submitted",
        unlocked=len(trailblazer_users) > 0, unlocked_by=trailblazer_users,
    ))

    # Speed Demon - 5 songs in one day
    speed_demon_users = []
    for username, songs in user_songs.items():
        day_counts: dict[str, int] = defaultdict(int)
        for job in songs:
            if job.created_at:
                day_counts[str(job.created_at.date())] += 1
        if any(c >= 5 for c in day_counts.values()):
            speed_demon_users.append(username)
    achievements.append(AchievementEntry(
        id="speed_demon", name="Speed Demon", emoji="⚡",
        description="Added 5 songs in a single day",
        unlocked=len(speed_demon_users) > 0, unlocked_by=speed_demon_users,
    ))

    return AchievementsResponse(achievements=achievements)


@router.get("/hipster", response_model=HipsterResponse)
async def get_hipster_index(db: DbSession) -> HipsterResponse:
    """Get hipster index - who adds the most obscure artists."""
    result = await db.execute(
        select(Job.requester_user_id, Job.artist)
        .where(Job.status == JobStatus.COMPLETE, Job.artist.isnot(None))
    )
    rows = result.all()

    # Count how many times each artist appears total
    artist_popularity: dict[str, int] = defaultdict(int)
    for row in rows:
        artist_popularity[row[1]] += 1

    # Per user, calculate average artist popularity (lower = more hipster)
    user_artists: dict[str, set] = defaultdict(set)
    for row in rows:
        username = _get_display_name(row[0])
        user_artists[username].add(row[1])

    entries = []
    for username, artists in user_artists.items():
        if not artists:
            continue
        avg_popularity = sum(artist_popularity[a] for a in artists) / len(artists)
        entries.append(HipsterEntry(
            username=username,
            color=USER_COLORS.get(username, "#6b7280"),
            unique_artists=len(artists),
            hipster_score=round(avg_popularity, 2),
        ))

    # Sort by hipster score (lower = more hipster)
    entries.sort(key=lambda e: e.hipster_score)
    return HipsterResponse(entries=entries)


@router.get("/streaks", response_model=StreaksResponse)
async def get_streaks(db: DbSession) -> StreaksResponse:
    """Get current and longest streaks per user."""
    result = await db.execute(
        select(Job.requester_user_id, Job.created_at)
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
        .order_by(Job.requester_user_id, Job.created_at)
    )
    rows = result.all()

    user_dates: dict[str, list] = defaultdict(list)
    for row in rows:
        username = _get_display_name(row[0])
        if row[1]:
            user_dates[username].append(row[1].date())

    today = datetime.now(timezone.utc).date()
    entries = []

    for username, dates in user_dates.items():
        unique_dates = sorted(set(dates))
        if not unique_dates:
            continue

        # Calculate longest streak
        longest = 1
        current = 1
        for i in range(1, len(unique_dates)):
            if (unique_dates[i] - unique_dates[i - 1]).days == 1:
                current += 1
                longest = max(longest, current)
            else:
                current = 1

        # Calculate current streak (counting back from today)
        current_streak = 0
        is_active = False
        if unique_dates[-1] == today or (today - unique_dates[-1]).days == 1:
            is_active = True
            current_streak = 1
            for i in range(len(unique_dates) - 2, -1, -1):
                if (unique_dates[i + 1] - unique_dates[i]).days == 1:
                    current_streak += 1
                else:
                    break

        entries.append(StreakEntry(
            username=username,
            color=USER_COLORS.get(username, "#6b7280"),
            current_streak=current_streak,
            longest_streak=longest,
            is_active=is_active,
        ))

    entries.sort(key=lambda e: e.longest_streak, reverse=True)
    return StreaksResponse(entries=entries)


@router.get("/personalities", response_model=PersonalitiesResponse)
async def get_personalities(db: DbSession) -> PersonalitiesResponse:
    """Get monthly personality cards based on submission patterns."""
    result = await db.execute(
        select(Job.requester_user_id, Job.source_platform, Job.artist)
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
    )
    rows = result.all()

    user_data: dict[str, dict] = defaultdict(lambda: {
        "platforms": defaultdict(int),
        "artists": set(),
        "total": 0,
    })

    for row in rows:
        username = _get_display_name(row[0])
        platform = row[1].value if hasattr(row[1], "value") else str(row[1])
        user_data[username]["platforms"][platform] += 1
        if row[2]:
            user_data[username]["artists"].add(row[2])
        user_data[username]["total"] += 1

    cards = []
    for username, data in user_data.items():
        total = data["total"]
        num_artists = len(data["artists"])
        dominant_platform = max(data["platforms"], key=data["platforms"].get) if data["platforms"] else "unknown"

        # Determine personality
        artist_ratio = num_artists / total if total > 0 else 0
        if total >= 20 and artist_ratio < 0.5:
            personality = "The Archivist"
            description = "Deep dives into favorite artists, building comprehensive collections"
        elif total >= 10 and dominant_platform == "spotify":
            personality = "The Populist"
            description = "Finger on the pulse of what's trending, always discovering hits"
        elif num_artists >= 15:
            personality = "The Explorer"
            description = "Always seeking new sounds, rarely submits the same artist twice"
        elif total >= 5:
            personality = "The Evangelist"
            description = "Passionate about sharing discoveries with the squad"
        else:
            personality = "The Newcomer"
            description = "Just getting started on their music sharing journey"

        cards.append(PersonalityCard(
            username=username,
            color=USER_COLORS.get(username, "#6b7280"),
            personality=personality,
            description=description,
            dominant_platform=dominant_platform,
            song_count=total,
        ))

    cards.sort(key=lambda c: c.song_count, reverse=True)
    return PersonalitiesResponse(cards=cards)


@router.get("/head-to-head/{user1}/{user2}", response_model=HeadToHeadResponse)
async def get_head_to_head(user1: str, user2: str, db: DbSession) -> HeadToHeadResponse:
    """Compare two users head to head."""
    # Get user IDs
    uid1 = None
    uid2 = None
    for uid, name in USER_DISPLAY_NAMES.items():
        if name == user1:
            uid1 = uid
        if name == user2:
            uid2 = uid

    async def get_user_data(user_id: str | None):
        if user_id is None:
            return [], set(), defaultdict(int)
        r = await db.execute(
            select(Job).where(Job.status == JobStatus.COMPLETE, Job.requester_user_id == user_id)
        )
        jobs = r.scalars().all()
        artists = set(j.artist for j in jobs if j.artist)
        platforms: dict[str, int] = defaultdict(int)
        for j in jobs:
            p = j.source_platform.value if j.source_platform else "unknown"
            platforms[p] += 1
        return jobs, artists, platforms

    jobs1, artists1, platforms1 = await get_user_data(uid1)
    jobs2, artists2, platforms2 = await get_user_data(uid2)

    shared = list(artists1 & artists2)[:10]
    unique1 = list(artists1 - artists2)[:10]
    unique2 = list(artists2 - artists1)[:10]

    return HeadToHeadResponse(
        user1=user1,
        user2=user2,
        user1_color=USER_COLORS.get(user1, "#6b7280"),
        user2_color=USER_COLORS.get(user2, "#6b7280"),
        user1_songs=len(jobs1),
        user2_songs=len(jobs2),
        user1_artists=len(artists1),
        user2_artists=len(artists2),
        user1_platforms=dict(platforms1),
        user2_platforms=dict(platforms2),
        shared_artists=shared,
        user1_unique_artists=unique1,
        user2_unique_artists=unique2,
    )


@router.get("/hall-of-fame", response_model=HallOfFameResponse)
async def get_hall_of_fame(db: DbSession) -> HallOfFameResponse:
    """Get hall of fame milestones."""
    entries = []

    # First song ever
    first_result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
        .order_by(Job.created_at.asc())
        .limit(1)
    )
    first_job = first_result.scalars().first()
    if first_job:
        entries.append(HallOfFameEntry(
            title="The Genesis",
            description="First song ever added to Slapshare",
            value=f"{first_job.title or 'Unknown'} by {first_job.artist or 'Unknown'} ({_get_display_name(first_job.requester_user_id)})",
            emoji="\U0001f31f",
        ))

    # Most submitted artist
    top_artist_result = await db.execute(
        select(Job.artist, func.count(Job.id).label("cnt"))
        .where(Job.status == JobStatus.COMPLETE, Job.artist.isnot(None))
        .group_by(Job.artist)
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    top_artist_row = top_artist_result.first()
    if top_artist_row:
        entries.append(HallOfFameEntry(
            title="Most Loved Artist",
            description="The artist with the most submissions",
            value=f"{top_artist_row[0]} ({top_artist_row[1]} songs)",
            emoji="\U0001f3b5",
        ))

    # Longest title
    longest_result = await db.execute(
        select(Job)
        .where(Job.status == JobStatus.COMPLETE, Job.title.isnot(None))
        .order_by(func.length(Job.title).desc())
        .limit(1)
    )
    longest_job = longest_result.scalars().first()
    if longest_job:
        entries.append(HallOfFameEntry(
            title="The Epic",
            description="Song with the longest title",
            value=f"{longest_job.title[:60]}{'...' if len(longest_job.title or '') > 60 else ''}",
            emoji="\U0001f4dc",
        ))

    # Most prolific day
    day_result = await db.execute(
        select(
            func.date(Job.created_at).label("sub_date"),
            func.count(Job.id).label("cnt"),
        )
        .where(Job.status == JobStatus.COMPLETE, Job.requester_user_id.notin_(BOT_USER_IDS))
        .group_by("sub_date")
        .order_by(func.count(Job.id).desc())
        .limit(1)
    )
    day_row = day_result.first()
    if day_row:
        entries.append(HallOfFameEntry(
            title="The Flood",
            description="Most songs added in a single day",
            value=f"{day_row[1]} songs on {day_row[0]}",
            emoji="\U0001f30a",
        ))

    return HallOfFameResponse(entries=entries)
