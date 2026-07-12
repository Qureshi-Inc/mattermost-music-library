"""Job pipeline - orchestrates the full music acquisition workflow.

Stages: resolve -> search -> score -> (approval) -> download -> tag -> organize -> refresh
"""

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path

from app.config import get_settings
from app.jobs.queue import JobQueue
from app.library.jellyfin import JellyfinClient
from app.library.organizer import LibraryOrganizer
from app.library.tagger import AudioTagger, TagData
from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)

_pipeline_instance: "JobPipeline | None" = None


def get_pipeline() -> "JobPipeline | None":
    """Return the running pipeline instance."""
    return _pipeline_instance


class JobPipeline:
    """Orchestrates the complete music acquisition pipeline.

    Each stage updates the job status and posts progress to Mattermost.
    Handles errors at each stage, marking jobs for retry on transient failures
    or permanent failure on unrecoverable errors.

    The pipeline flow:
        1. Resolve - extract metadata from the source URL
        2. Search - find YouTube candidates matching the metadata
        3. Score - rank candidates by match quality
        4. Approve - auto-approve >= 0.90, manual review 0.70-0.90, reject < 0.70
        5. Download - fetch the approved candidate via yt-dlp
        6. Tag - apply ID3v2.4 tags to the downloaded MP3
        7. Organize - move file into library structure
        8. Refresh - trigger Jellyfin to scan for new content
    """

    def __init__(
        self,
        queue: JobQueue,
        mattermost_client: object | None = None,
        auto_approve: bool = True,
    ) -> None:
        """Initialize the pipeline with its dependencies.

        Args:
            queue: The JobQueue for status management.
            mattermost_client: Optional MattermostClient for status updates.
            auto_approve: Whether to auto-approve high-confidence matches.
        """
        self.queue = queue
        self.mattermost = mattermost_client
        self.auto_approve = auto_approve
        self._status_post_ids: dict[str, str] = {}  # job_id -> mattermost post_id
        self._settings = get_settings()
        self._tagger = AudioTagger()
        self._organizer = LibraryOrganizer(self._settings.music_base_path)
        self._jellyfin = JellyfinClient(
            self._settings.jellyfin_url, self._settings.jellyfin_token
        )
        self._running = False
        self._processing_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the pipeline processing loop."""
        global _pipeline_instance
        self._running = True
        self._processing_task = asyncio.create_task(self._process_loop())
        _pipeline_instance = self
        logger.info("Job pipeline started")

    async def stop(self) -> None:
        """Stop the pipeline processing loop gracefully."""
        self._running = False
        if self._processing_task:
            self._processing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._processing_task
        await self._jellyfin.close()
        logger.info("Job pipeline stopped")

    async def _process_loop(self) -> None:
        """Main processing loop - polls for pending jobs and processes them."""
        while self._running:
            try:
                pending_jobs = await self.queue.get_pending_jobs()
                if pending_jobs:
                    logger.info("Found %d pending jobs", len(pending_jobs))
                for job in pending_jobs:
                    if not self._running:
                        break

                    # Skip jobs that need retry delay
                    if job.retry_count > 0:
                        import datetime
                        delay = await self.queue.get_retry_delay(job)
                        if job.updated_at:
                            now = datetime.datetime.utcnow()
                            updated = job.updated_at.replace(tzinfo=None) if job.updated_at.tzinfo else job.updated_at
                            elapsed = (now - updated).total_seconds()
                            if elapsed < delay:
                                logger.debug("Job %s needs %.0fs more delay", job.id, delay - elapsed)
                                continue

                    logger.info("Processing job %s (retry=%d)", job.id, job.retry_count)
                    await self.process_job(job.id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in pipeline loop", exc_info=True, extra={"error": str(e)})

            # Poll interval
            await asyncio.sleep(2.0)

    async def process_job(self, job_id: uuid.UUID) -> None:
        """Process a single job through the entire pipeline.

        Each stage is wrapped in error handling. On failure, the job
        is marked for retry or permanent failure depending on the error.

        Args:
            job_id: The UUID of the job to process.
        """
        job = await self.queue.get_job(job_id)
        if job is None:
            logger.error("Job not found for processing", extra={"job_id": str(job_id)})
            return

        if job.status not in (JobStatus.PENDING, JobStatus.APPROVED):
            logger.info(
                "Job not in processable state",
                extra={"job_id": str(job_id), "status": job.status.value},
            )
            return

        try:
            # Stage 1: Resolve metadata from source URL
            metadata = await self._stage_resolve(job)
            if metadata is None:
                return

            # Stage 1.5: Check for duplicates
            if await self._check_duplicate(job, metadata):
                return

            # Stage 2: Search for YouTube candidates
            candidates = await self._stage_search(job, metadata)
            if candidates is None:
                return

            # Stage 3: Score candidates
            best_candidate = await self._stage_score(job, candidates)
            if best_candidate is None:
                return

            # Stage 4: Approval gate
            approved = await self._stage_approve(job, best_candidate)
            if not approved:
                return

            # Stage 5: Download the approved candidate
            download_path = await self._stage_download(job, best_candidate)
            if download_path is None:
                return

            # Stage 6: Tag the downloaded file
            tagged_path = await self._stage_tag(job, download_path, metadata)
            if tagged_path is None:
                return

            # Stage 7: Organize into library
            final_path = await self._stage_organize(job, tagged_path, metadata)
            if final_path is None:
                return

            # Stage 8: Refresh Jellyfin
            await self._stage_refresh(job)

            # Mark complete
            await self.queue.update_status(job.id, JobStatus.COMPLETE)
            title = metadata.get("title", "Unknown")
            artist = metadata.get("artist", "Unknown")
            album = metadata.get("album", "Unknown Album")
            await self._post_status(
                job,
                f"✅ Added **{title}** by **{artist}**\n\n"
                f"🎧 Available in Jellyfin / Finamp\n"
                f"📁 {artist}/{album}/{title}.mp3",
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Unhandled pipeline error",
                extra={"job_id": str(job_id), "error": str(e)},
                exc_info=True,
            )
            await self.queue.mark_failed(job_id, f"Unhandled error: {str(e)}")
            await self._post_status(job, f"Failed: {str(e)}")

    async def _check_duplicate(self, job: Job, metadata: dict) -> bool:
        """Check if a song with the same title/artist already exists in the library.

        Returns True if duplicate found (job should be skipped).
        """
        title = metadata.get("title")
        artist = metadata.get("artist")
        if not title or not artist:
            return False

        # Normalize for comparison
        norm_title = title.lower().strip()
        norm_artist = artist.lower().strip()

        # Check filesystem
        music_path = self._settings.music_base_path
        if music_path.exists():
            for mp3 in music_path.rglob("*.mp3"):
                fname = mp3.stem.lower()
                parent = mp3.parent.parent.name.lower()  # Artist folder
                if norm_title in fname and norm_artist in parent:
                    await self.queue.update_status(job.id, JobStatus.COMPLETE)
                    await self._post_status(
                        job,
                        f"ℹ️ Already in library: **{title}** by **{artist}**",
                    )
                    logger.info("Duplicate found", extra={"job_id": str(job.id), "title": title, "artist": artist})
                    return True

        return False

    async def _stage_resolve(self, job: Job) -> dict | None:
        """Stage 1: Resolve metadata from the source URL.

        Returns:
            Dict with metadata keys, or None if resolution failed.
        """
        await self.queue.update_status(job.id, JobStatus.RESOLVING)
        await self._post_status(job, "Resolving metadata from link...")

        try:
            # Import resolver dynamically to avoid circular imports

            # Find appropriate resolver for this URL
            resolver = await self._get_resolver(job.url)
            if resolver is None:
                await self.queue.mark_failed(job.id, "No resolver available for this URL")
                await self._post_status(job, "No resolver found for this link type")
                return None

            track_metadata = await resolver.resolve(job.url)  # type: ignore[attr-defined]

            if not track_metadata.has_minimum:
                await self.queue.mark_failed(job.id, "Could not resolve any metadata from URL")
                await self._post_status(job, "Could not extract metadata from link")
                return None

            # Update job with resolved metadata
            await self.queue.update_status(
                job.id,
                JobStatus.RESOLVING,
                title=track_metadata.title,
                artist=track_metadata.artist,
                album=track_metadata.album,
            )

            return {
                "title": track_metadata.title,
                "artist": track_metadata.artist,
                "album": track_metadata.album,
                "duration_seconds": track_metadata.duration_seconds,
                "isrc": track_metadata.isrc,
                "extra": getattr(track_metadata, "extra", None) or {},
            }

        except Exception as e:
            logger.error(
                "Resolve stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"Resolve failed: {str(e)}")
            await self._post_status(job, f"Failed to resolve metadata: {str(e)}")
            return None

    async def _stage_search(self, job: Job, metadata: dict) -> list | None:
        """Stage 2: Search YouTube for candidates matching the metadata.

        Returns:
            List of candidate results, or None if search failed.
        """
        await self.queue.update_status(job.id, JobStatus.SEARCHING)
        await self._post_status(
            job,
            f"Searching for: {metadata.get('artist', '')} - {metadata.get('title', '')}",
        )

        try:
            import asyncio
            from app.matching import YouTubeSearcher

            searcher = YouTubeSearcher()
            title = metadata.get("title") or "unknown"
            artist = metadata.get("artist") or "unknown"
            candidates_result = await asyncio.to_thread(searcher.search, artist, title)
            candidates = candidates_result.candidates if candidates_result else []

            if not candidates:
                await self.queue.mark_failed(job.id, "No YouTube candidates found")
                await self._post_status(job, "No matching videos found on YouTube")
                return None

            return candidates  # type: ignore[no-any-return]

        except Exception as e:
            logger.error(
                "Search stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"Search failed: {str(e)}")
            await self._post_status(job, f"Search failed: {str(e)}")
            return None

    async def _stage_score(self, job: Job, candidates: list) -> object | None:
        """Stage 3: Score and rank candidates.

        Candidates are already scored by the searcher. This stage picks the best one.

        Returns:
            The best scoring candidate, or None if all candidates are rejected.
        """
        try:
            if not candidates:
                await self.queue.mark_failed(job.id, "All candidates rejected during scoring")
                await self._post_status(job, "No suitable match found")
                return None

            # Candidates are already scored and sorted by the searcher
            best = candidates[0]
            score = getattr(best, "score", 0) if hasattr(best, "score") else (best.get("score", 0) if isinstance(best, dict) else 0)
            logger.info("Best candidate score: %.3f for job %s", score, job.id)
            return best

        except Exception as e:
            logger.error(
                "Score stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"Scoring failed: {str(e)}")
            await self._post_status(job, f"Scoring failed: {str(e)}")
            return None

    async def _stage_approve(self, job: Job, candidate: object) -> bool:
        """Stage 4: Approval gate based on candidate score.

        Auto-approves scores >= auto_approve_threshold (default 0.90).
        Requires manual review for scores between manual_review_threshold and auto_approve_threshold.
        Rejects scores below manual_review_threshold (default 0.70).

        Returns:
            True if approved, False if rejected or waiting for manual review.
        """
        score = candidate.get("score", 0) if isinstance(candidate, dict) else getattr(candidate, "score", 0)

        auto_threshold = self._settings.auto_approve_threshold
        review_threshold = self._settings.manual_review_threshold

        if score >= auto_threshold and self.auto_approve:
            # Auto-approve high confidence matches
            await self.queue.update_status(job.id, JobStatus.APPROVED)
            await self._post_status(
                job, f"Auto-approved (score: {score:.2f})"
            )
            return True

        elif score >= review_threshold:
            # Needs manual review
            await self.queue.update_status(job.id, JobStatus.REVIEWING)
            candidate_title = (
                candidate.get("title", "Unknown")
                if isinstance(candidate, dict)
                else getattr(candidate, "title", "Unknown")
            )
            await self._post_status(
                job,
                f"Needs approval (score: {score:.2f}): **{candidate_title}**\n"
                f"React with :white_check_mark: to approve or :x: to reject.",
            )
            # Job stays in REVIEWING until manual action
            return False

        else:
            # Score too low - reject
            await self.queue.mark_failed(
                job.id,
                f"Best candidate score ({score:.2f}) below threshold ({review_threshold})",
            )
            await self._post_status(
                job,
                f"No good match found (best score: {score:.2f}, threshold: {review_threshold})",
            )
            return False

    async def _stage_download(self, job: Job, candidate: object) -> Path | None:
        """Stage 5: Download the approved candidate via yt-dlp.

        Returns:
            Path to the downloaded file, or None on failure.
        """
        await self.queue.update_status(job.id, JobStatus.DOWNLOADING)
        await self._post_status(job, "Downloading...")

        try:
            # Get the YouTube URL from the candidate
            youtube_url = (
                candidate.get("youtube_url", candidate.get("url", ""))
                if isinstance(candidate, dict)
                else getattr(candidate, "youtube_url", getattr(candidate, "url", ""))
            )

            if not youtube_url:
                await self.queue.mark_failed(job.id, "No download URL on candidate")
                return None

            # Use yt-dlp to download
            import tempfile

            temp_dir = tempfile.mkdtemp(prefix="slaptastic_")
            opts = {
                **self._settings.ytdlp_opts,
                "outtmpl": f"{temp_dir}/%(title)s.%(ext)s",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(self._settings.mp3_bitrate),
                    }
                ],
            }

            loop = asyncio.get_event_loop()
            downloaded_file = await loop.run_in_executor(
                None, self._download_sync, youtube_url, opts, temp_dir
            )

            if downloaded_file is None:
                await self.queue.mark_failed(job.id, "Download produced no output file")
                await self._post_status(job, "Download failed - no output file")
                return None

            return downloaded_file

        except Exception as e:
            logger.error(
                "Download stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"Download failed: {str(e)}")
            await self._post_status(job, f"Download failed: {str(e)}")
            return None

    @staticmethod
    def _download_sync(url: str, opts: dict, temp_dir: str) -> Path | None:
        """Synchronous yt-dlp download (run in executor).

        Args:
            url: YouTube URL to download.
            opts: yt-dlp options dict.
            temp_dir: Directory for output.

        Returns:
            Path to the downloaded MP3, or None.
        """
        from pathlib import Path as _Path

        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # Find the downloaded MP3 in temp_dir
            mp3_files = list(_Path(temp_dir).glob("*.mp3"))
            if mp3_files:
                return mp3_files[0]
            return None
        except Exception as e:
            logger.error("yt-dlp download error", extra={"error": str(e)})
            return None

    async def _stage_tag(
        self, job: Job, file_path: Path, metadata: dict
    ) -> Path | None:
        """Stage 6: Apply ID3v2.4 tags to the downloaded MP3.

        Returns:
            The same file path (now tagged), or None on failure.
        """
        await self.queue.update_status(job.id, JobStatus.PROCESSING)
        await self._post_status(job, "Tagging file...")

        try:
            extra = metadata.get("extra", {}) or {}
            artwork_url = extra.get("artwork_url")

            # Fallback: use YouTube thumbnail if no artwork from metadata
            if not artwork_url:
                video_id = metadata.get("youtube_video_id") or extra.get("youtube_video_id")
                if not video_id:
                    import re
                    yt_match = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]+)", job.url or "")
                    if yt_match:
                        video_id = yt_match.group(1)
                if video_id:
                    artwork_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

            tag_data = TagData(
                title=metadata.get("title"),
                artist=metadata.get("artist"),
                album=metadata.get("album"),
                album_artist=metadata.get("artist"),
                track_number=extra.get("track_number"),
                disc_number=extra.get("disc_number"),
                year=extra.get("release_date", "")[:4] if extra.get("release_date") else None,
                genre=extra.get("genre"),
                isrc=metadata.get("isrc"),
                artwork_url=artwork_url,
                source_url=job.url,
            )

            self._tagger.tag_file(file_path, tag_data)
            return file_path

        except Exception as e:
            logger.error(
                "Tag stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"Tagging failed: {str(e)}")
            await self._post_status(job, f"Tagging failed: {str(e)}")
            return None

    async def _stage_organize(
        self, job: Job, file_path: Path, metadata: dict
    ) -> Path | None:
        """Stage 7: Move the file into the library structure.

        Returns:
            The final library path, or None on failure.
        """
        try:
            artist = metadata.get("artist") or "Unknown Artist"
            album = metadata.get("album") or "Unknown Album"
            title = metadata.get("title") or "Unknown Track"

            final_path = self._organizer.organize(
                source_path=file_path,
                artist=artist,
                album=album,
                title=title,
                track_number=None,  # Usually not available from single-track downloads
                move=True,
            )

            # Update the job with the final file path info
            await self.queue.update_status(job.id, JobStatus.PROCESSING)

            return final_path

        except Exception as e:
            logger.error(
                "Organize stage failed",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            await self.queue.mark_failed(job.id, f"File organization failed: {str(e)}")
            await self._post_status(job, f"Organization failed: {str(e)}")
            return None

    async def _stage_refresh(self, job: Job) -> None:
        """Stage 8: Trigger Jellyfin to scan for the new content."""
        try:
            success = await self._jellyfin.refresh_music_library()
            if success:
                logger.info(
                    "Jellyfin refresh triggered after job completion",
                    extra={"job_id": str(job.id)},
                )
            else:
                # Non-fatal - the file is in the library, Jellyfin will pick it up eventually
                logger.warning(
                    "Jellyfin refresh failed (non-fatal)",
                    extra={"job_id": str(job.id)},
                )
        except Exception as e:
            logger.warning(
                "Jellyfin refresh error (non-fatal)",
                extra={"job_id": str(job.id), "error": str(e)},
            )

    async def _get_resolver(self, url: str) -> object | None:
        """Find an appropriate resolver for the given URL.

        Returns:
            A resolver instance that can handle this URL, or None.
        """
        try:

            # Try to import platform-specific resolvers
            resolvers: list = []

            try:
                from app.resolvers.spotify import SpotifyResolver
                resolvers.append(SpotifyResolver())
            except ImportError:
                pass

            try:
                from app.resolvers.apple_music import AppleMusicResolver
                resolvers.append(AppleMusicResolver())
            except ImportError:
                pass

            try:
                from app.resolvers.youtube import YouTubeResolver
                resolvers.append(YouTubeResolver())
            except ImportError:
                pass

            for resolver in resolvers:
                if resolver.can_handle(url):
                    return resolver  # type: ignore[no-any-return]

            return None

        except Exception as e:
            logger.error("Error loading resolvers", extra={"error": str(e)})
            return None

    def _build_search_query(self, metadata: dict) -> str:
        """Build a YouTube search query from resolved metadata.

        Args:
            metadata: Dict with title, artist, album keys.

        Returns:
            Search query string.
        """
        parts = []
        if metadata.get("artist"):
            parts.append(metadata["artist"])
        if metadata.get("title"):
            parts.append(metadata["title"])
        if not parts:
            # Fallback - shouldn't happen if resolve succeeded
            parts.append("unknown track")
        return " ".join(parts)

    async def _post_status(self, job: Job, message: str) -> None:
        """Post or update a status message in Mattermost for this job.

        Creates a single thread reply on first call, then edits that same
        message on subsequent calls. This keeps the thread clean.

        Args:
            job: The job this status is about.
            message: The status message to post/update.
        """
        if self.mattermost is None:
            return

        if not job.mattermost_channel_id:
            return

        job_key = str(job.id)

        try:
            existing_post_id = self._status_post_ids.get(job_key)

            if existing_post_id:
                await self.mattermost.update_post(existing_post_id, message)  # type: ignore[attr-defined]
            else:
                # Wait briefly for the listener to store the post ID
                await asyncio.sleep(0.5)
                existing_post_id = self._status_post_ids.get(job_key)
                if existing_post_id:
                    await self.mattermost.update_post(existing_post_id, message)  # type: ignore[attr-defined]
                else:
                    # Last resort — create a new post
                    result = await self.mattermost.post_message(  # type: ignore[attr-defined]
                        channel_id=job.mattermost_channel_id,
                        message=message,
                        root_id=job.mattermost_post_id or "",
                    )
                    if result and result.get("id"):
                        self._status_post_ids[job_key] = result["id"]
        except Exception as e:
            logger.warning(
                "Failed to post status to Mattermost",
                extra={"job_id": str(job.id), "error": str(e)},
            )
