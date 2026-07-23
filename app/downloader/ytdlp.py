"""YtDlpDownloader - downloads audio from YouTube using yt-dlp Python API."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yt_dlp

if TYPE_CHECKING:
    from app.downloader.progress import DownloadProgressTracker

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when a download fails."""

    pass


class GeoRestrictedError(DownloadError):
    """Raised when content is geo-restricted."""

    pass


class ContentRemovedError(DownloadError):
    """Raised when content has been removed."""

    pass


class AgeRestrictedError(DownloadError):
    """Raised when content is age-restricted and cannot be accessed."""

    pass


class YtDlpDownloader:
    """Downloads audio from YouTube using yt-dlp as a Python library.

    Downloads to a temp directory first, converts to MP3 using yt-dlp's
    FFmpeg postprocessor, then moves to the final destination.
    No shell=True anywhere - uses yt-dlp's Python API directly.
    """

    def __init__(
        self,
        bitrate: int = 320,
        progress_tracker: DownloadProgressTracker | None = None,
        embed_metadata: bool = True,
        embed_thumbnail: bool = True,
        concurrent_fragments: int = 4,
        rate_limit: str | None = None,
    ) -> None:
        """Initialize the downloader.

        Args:
            bitrate: Target MP3 bitrate in kbps (default 320).
            progress_tracker: Optional progress tracker for reporting download status.
            embed_metadata: Whether to embed metadata tags in the output file.
            embed_thumbnail: Whether to embed thumbnail art in the output file.
            concurrent_fragments: Number of concurrent download fragments.
            rate_limit: Download rate limit string (e.g. '5M' for 5MB/s).
        """
        self.bitrate = bitrate
        self.progress_tracker = progress_tracker
        self.embed_metadata = embed_metadata
        self.embed_thumbnail = embed_thumbnail
        self.concurrent_fragments = concurrent_fragments
        self.rate_limit = rate_limit

    def download(self, url: str, output_dir: Path, filename: str | None = None) -> Path:
        """Download audio from a YouTube URL and convert to MP3.

        Downloads to a temporary directory first, then moves the final MP3
        to the output directory.

        Args:
            url: YouTube video URL to download.
            output_dir: Directory where the final MP3 file will be placed.
            filename: Optional filename (without extension) for the output.
                      If None, yt-dlp's default naming is used.

        Returns:
            Path to the downloaded MP3 file.

        Raises:
            GeoRestrictedError: If the video is geo-restricted.
            ContentRemovedError: If the video has been removed.
            AgeRestrictedError: If the video is age-restricted.
            DownloadError: For other download failures.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="slaptastic_dl_") as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Configure output template
            if filename:
                outtmpl = str(tmp_path / f"{filename}.%(ext)s")
            else:
                outtmpl = str(tmp_path / "%(title)s.%(ext)s")

            # Build yt-dlp options
            ydl_opts = self._build_options(outtmpl)

            logger.info("Downloading: %s", url)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                    if info is None:
                        raise DownloadError(f"Failed to extract info from URL: {url}")

            except yt_dlp.utils.DownloadError as exc:
                self._handle_ytdlp_error(exc, url)

            # Find the downloaded MP3 file in the temp directory
            mp3_files = list(tmp_path.glob("*.mp3"))
            if not mp3_files:
                # Check for other audio formats in case conversion failed
                audio_files = list(tmp_path.glob("*.m4a")) + list(tmp_path.glob("*.opus")) + list(tmp_path.glob("*.webm"))
                if audio_files:
                    raise DownloadError(
                        f"Audio downloaded but MP3 conversion failed. "
                        f"Found: {[f.name for f in audio_files]}"
                    )
                raise DownloadError(f"No audio file found after download of {url}")

            # Use the first (should be only) MP3 file
            downloaded_file = mp3_files[0]

            # Move to final destination
            final_path = output_dir / downloaded_file.name
            shutil.move(str(downloaded_file), str(final_path))

            logger.info("Download complete: %s", final_path)

            if self.progress_tracker:
                self.progress_tracker.complete(final_path)

            return final_path

    def _build_options(self, outtmpl: str) -> dict:
        """Build the yt-dlp options dictionary.

        Uses yt-dlp's postprocessor system for FFmpeg conversion rather
        than calling FFmpeg via subprocess.
        """
        postprocessors = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(self.bitrate),
            },
        ]

        if self.embed_metadata:
            postprocessors.append({"key": "FFmpegMetadata"})

        if self.embed_thumbnail:
            postprocessors.append({"key": "EmbedThumbnail"})

        opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": postprocessors,
            "quiet": True,
            "no_warnings": True,
            "noprogress": not bool(self.progress_tracker),
            "concurrent_fragment_downloads": self.concurrent_fragments,
            "writethumbnail": self.embed_thumbnail,
            # Avoid interactive prompts
            "noplaylist": True,
            "no_color": True,
            # YouTube now requires solving JS challenges (via the deno runtime in
            # the image). Allow yt-dlp to fetch the EJS challenge-solver from
            # GitHub, otherwise many videos fail extraction with HTTP 403.
            "remote_components": ["ejs:github"],
        }

        if self.rate_limit:
            opts["ratelimit"] = self.rate_limit

        # Attach progress hook if tracker is provided
        if self.progress_tracker:
            opts["progress_hooks"] = [self._progress_hook]

        return opts

    def _progress_hook(self, d: dict) -> None:
        """yt-dlp progress hook that reports to the progress tracker."""
        if self.progress_tracker is None:
            return

        status = d.get("status")

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)

            if total and total > 0:
                percentage = (downloaded / total) * 100.0
                self.progress_tracker.update(
                    percentage=percentage,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=d.get("speed"),
                    eta=d.get("eta"),
                )
            else:
                # We have no total, report bytes downloaded
                self.progress_tracker.update(
                    percentage=None,
                    downloaded_bytes=downloaded,
                    total_bytes=None,
                    speed=d.get("speed"),
                    eta=d.get("eta"),
                )

        elif status == "finished":
            self.progress_tracker.update(
                percentage=100.0,
                downloaded_bytes=d.get("total_bytes") or d.get("downloaded_bytes", 0),
                total_bytes=d.get("total_bytes"),
                speed=None,
                eta=0,
                phase="postprocessing",
            )

        elif status == "error":
            self.progress_tracker.error(d.get("error", "Unknown download error"))

    def _handle_ytdlp_error(self, exc: yt_dlp.utils.DownloadError, url: str) -> None:
        """Map yt-dlp exceptions to our domain-specific exceptions.

        Raises:
            GeoRestrictedError: For geo-restriction errors.
            ContentRemovedError: For removed/unavailable content.
            AgeRestrictedError: For age-restricted content.
            DownloadError: For all other errors.
        """
        error_msg = str(exc).lower()

        if "geo" in error_msg or "not available in your country" in error_msg:
            raise GeoRestrictedError(
                f"Video is geo-restricted and cannot be downloaded: {url}"
            ) from exc

        if (
            "has been removed" in error_msg
            or "video unavailable" in error_msg
            or "private video" in error_msg
            or "this video is no longer available" in error_msg
        ):
            raise ContentRemovedError(
                f"Video has been removed or is unavailable: {url}"
            ) from exc

        if "age" in error_msg and ("restrict" in error_msg or "gate" in error_msg):
            raise AgeRestrictedError(
                f"Video is age-restricted and cannot be accessed: {url}"
            ) from exc

        if "sign in" in error_msg or "login" in error_msg:
            raise AgeRestrictedError(
                f"Video requires authentication (likely age-restricted): {url}"
            ) from exc

        raise DownloadError(f"Download failed for {url}: {exc}") from exc
