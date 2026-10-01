"""Reroll a library track: search again, and swap in a better download.

The pipeline auto-picks the top-scoring YouTube upload, which is sometimes the
wrong song (a cover, a different track with the same name). These two endpoints
let a listener fix that from the player:

* ``POST /tracks/research`` - search YouTube for the track again and return the
  scored candidates, or run a free-text search.
* ``POST /tracks/replace`` - download one candidate and put it in place of an
  existing library file. The old file's tags and cover art are copied onto the
  new one, so the track keeps its title, album and position, and the old file is
  kept under ``REPLACED_DIR`` so a bad swap can be undone by hand.

Both require the admin token; the CRCMZ app calls them on a signed-in member's behalf.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, status
from mutagen.id3 import ID3, ID3NoHeaderError
from pydantic import BaseModel, Field

from app.api.deps import AdminToken
from app.config import get_settings
from app.security.validation import ValidationError, validate_safe_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tracks", tags=["tracks"])

REPLACED_DIR = Path(os.environ.get("REPLACED_DIR", "/app/data/replaced"))
# Where a replacement may be downloaded from: YouTube and SoundCloud pages only.
SOURCE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
                "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
SEARCH_TIMEOUT_S = 90
DOWNLOAD_TIMEOUT_S = 300

# One swap at a time: two replaces of the same file must not interleave.
_replace_lock = asyncio.Lock()


class ResearchRequest(BaseModel):
    title: str = Field("", max_length=300)
    artist: str = Field("", max_length=300)
    duration_seconds: float | None = Field(None, ge=0, le=6 * 3600)
    # Free text instead of artist + title ("sahara hasan raheem official audio").
    query: str = Field("", max_length=300)
    limit: int = Field(8, ge=1, le=15)


class Candidate(BaseModel):
    url: str
    title: str
    channel: str
    duration_seconds: float | None
    view_count: int | None
    score: float


class ResearchResponse(BaseModel):
    query: str
    candidates: list[Candidate]


class ReplaceRequest(BaseModel):
    # Relative to the music library root, e.g. "Artist/Album/Song.mp3".
    path: str = Field(..., min_length=1, max_length=1024)
    url: str = Field(..., min_length=1, max_length=2048)


class ReplaceResponse(BaseModel):
    path: str
    backup: str
    duration_seconds: float | None


def source_url(url: str) -> str:
    """The URL if it is an http(s) YouTube or SoundCloud page, else ValueError."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in SOURCE_HOSTS:
        raise ValueError("Use a YouTube or SoundCloud link")
    return u.geturl()


def library_file(rel: str) -> Path:
    """The existing MP3 at ``rel`` inside the library, else ValueError."""
    try:
        path = validate_safe_path(rel, get_settings().music_base_path)
    except ValidationError as e:
        raise ValueError(e.message) from None
    if path.suffix.lower() != ".mp3" or not path.is_file():
        raise ValueError("No such track in the library")
    return path


def _search(req: ResearchRequest) -> tuple[str, list]:
    from app.matching import YouTubeSearcher
    from app.matching.scorer import ExpectedMetadata

    searcher = YouTubeSearcher(max_results=max(req.limit, 8))
    if req.query.strip():
        # Free text: no expectations to score against, so keep YouTube's order.
        q = req.query.strip()
        return q, searcher._fetch_candidates(f"ytsearch{searcher.max_results}:{q}")
    title_l = req.title.lower()
    expected = ExpectedMetadata(
        title=req.title, artist=req.artist, duration_seconds=req.duration_seconds,
        is_live="live" in title_l, is_remix="remix" in title_l, is_cover="cover" in title_l,
    )
    result = searcher.search(req.artist, req.title, expected)
    return result.query, result.candidates


@router.post("/research", response_model=ResearchResponse)
async def research(req: ResearchRequest, _: AdminToken) -> ResearchResponse:
    if not req.query.strip() and not req.title.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give a title or a search")
    try:
        query, found = await asyncio.wait_for(asyncio.to_thread(_search, req), timeout=SEARCH_TIMEOUT_S)
    except TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "YouTube search timed out") from None
    return ResearchResponse(query=query, candidates=[
        Candidate(url=c.url, title=c.title, channel=c.channel, duration_seconds=c.duration,
                  view_count=c.view_count, score=round(float(c.score or 0), 3))
        for c in found[: req.limit]
    ])


def _download(url: str) -> tuple[Path, str]:
    """Download ``url`` as an MP3 into a fresh temp dir: (file, temp dir)."""
    from app.jobs.pipeline import JobPipeline

    settings = get_settings()
    temp_dir = tempfile.mkdtemp(prefix="slaptastic_reroll_")
    opts = {
        **settings.ytdlp_opts,
        "outtmpl": f"{temp_dir}/%(id)s.%(ext)s",
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3",
            "preferredquality": str(settings.mp3_bitrate),
        }],
    }
    got = JobPipeline._download_sync(url, opts, temp_dir)
    if got is None:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("The download produced no audio")
    return got, temp_dir


def swap_in(new: Path, target: Path, backup_root: Path) -> Path:
    """Put ``new`` at ``target``, carrying over target's tags; returns the backup path."""
    try:
        tags = ID3(str(target))
    except ID3NoHeaderError:
        tags = None
    if tags is not None:
        # The old file's title/artist/album/art are right; only the audio was wrong.
        tags.delall("TLEN")
        tags.save(str(new), v2_version=4)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backup_root / stamp / target.relative_to(get_settings().music_base_path.resolve())
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    # Copy next to the target first so the final step is an atomic rename on one filesystem.
    staged = target.with_name(f".{target.name}.reroll")
    shutil.copyfile(new, staged)
    os.replace(staged, target)
    return backup


def _duration(path: Path) -> float | None:
    try:
        from mutagen.mp3 import MP3
        return round(float(MP3(str(path)).info.length), 1)
    except Exception:
        return None


@router.post("/replace", response_model=ReplaceResponse)
async def replace(req: ReplaceRequest, _: AdminToken) -> ReplaceResponse:
    try:
        url = source_url(req.url)
        target = library_file(req.path)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    if _replace_lock.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "Another song is being replaced; try again in a minute")
    async with _replace_lock:
        try:
            new, temp_dir = await asyncio.wait_for(asyncio.to_thread(_download, url), timeout=DOWNLOAD_TIMEOUT_S)
        except TimeoutError:
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "The download took too long") from None
        except Exception as e:
            logger.warning("reroll download failed", extra={"url": url, "error": str(e)})
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't download that link") from None
        try:
            backup = await asyncio.to_thread(swap_in, new, target, REPLACED_DIR)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    logger.info("reroll replaced track", extra={"path": req.path, "url": url, "backup": str(backup)})
    return ReplaceResponse(path=req.path, backup=str(backup), duration_seconds=_duration(target))
