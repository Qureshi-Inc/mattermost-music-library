"""Download progress tracker - reports download progress back to the job system."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ProgressUpdate:
    """A snapshot of download progress at a point in time."""

    percentage: float | None  # 0.0 - 100.0, or None if unknown
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None  # bytes per second
    eta: int | None  # seconds remaining
    phase: str  # "downloading", "postprocessing", "complete", "error"
    timestamp: float = field(default_factory=time.time)
    error_message: str | None = None

    @property
    def speed_human(self) -> str:
        """Human-readable download speed."""
        if self.speed is None:
            return "unknown"
        if self.speed < 1024:
            return f"{self.speed:.0f} B/s"
        elif self.speed < 1024 * 1024:
            return f"{self.speed / 1024:.1f} KB/s"
        else:
            return f"{self.speed / (1024 * 1024):.1f} MB/s"

    @property
    def eta_human(self) -> str:
        """Human-readable ETA."""
        if self.eta is None:
            return "unknown"
        if self.eta < 60:
            return f"{self.eta}s"
        elif self.eta < 3600:
            return f"{self.eta // 60}m {self.eta % 60}s"
        else:
            hours = self.eta // 3600
            minutes = (self.eta % 3600) // 60
            return f"{hours}h {minutes}m"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for JSON-compatible job reporting."""
        return {
            "percentage": round(self.percentage, 1) if self.percentage is not None else None,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed": self.speed_human,
            "eta": self.eta_human,
            "phase": self.phase,
            "timestamp": self.timestamp,
            "error_message": self.error_message,
        }


class DownloadProgressTracker:
    """Tracks download progress and reports it to the job system.

    Supports callback-based reporting so the job system can receive
    real-time progress updates. Throttles updates to avoid flooding
    the job system with too-frequent messages.
    """

    def __init__(
        self,
        job_id: str | None = None,
        callback: Callable[[ProgressUpdate], None] | None = None,
        min_update_interval: float = 0.5,
    ) -> None:
        """Initialize the progress tracker.

        Args:
            job_id: Identifier for the job this download belongs to.
            callback: Function called with each progress update. This is the
                      primary mechanism for reporting progress to the job system.
            min_update_interval: Minimum seconds between progress callbacks
                                 to avoid flooding (default 0.5s).
        """
        self.job_id = job_id
        self.callback = callback
        self.min_update_interval = min_update_interval

        self._last_update_time: float = 0.0
        self._latest: ProgressUpdate | None = None
        self._started_at: float = time.time()
        self._completed: bool = False
        self._history: list[ProgressUpdate] = []

    @property
    def latest(self) -> ProgressUpdate | None:
        """The most recent progress update."""
        return self._latest

    @property
    def is_complete(self) -> bool:
        """Whether the download has completed (successfully or with error)."""
        return self._completed

    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed time since tracking started."""
        return time.time() - self._started_at

    @property
    def history(self) -> list[ProgressUpdate]:
        """All recorded progress updates."""
        return list(self._history)

    def update(
        self,
        percentage: float | None,
        downloaded_bytes: int = 0,
        total_bytes: int | None = None,
        speed: float | None = None,
        eta: int | None = None,
        phase: str = "downloading",
    ) -> None:
        """Record a progress update.

        Called by the downloader's progress hook. Throttles callback invocations
        to respect min_update_interval.

        Args:
            percentage: Download percentage (0-100), or None if unknown.
            downloaded_bytes: Bytes downloaded so far.
            total_bytes: Total bytes expected, or None if unknown.
            speed: Current download speed in bytes/sec.
            eta: Estimated seconds remaining.
            phase: Current phase ("downloading" or "postprocessing").
        """
        now = time.time()

        progress = ProgressUpdate(
            percentage=percentage,
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            speed=speed,
            eta=eta,
            phase=phase,
            timestamp=now,
        )

        self._latest = progress
        self._history.append(progress)

        # Throttle callback invocations
        if self.callback and (now - self._last_update_time) >= self.min_update_interval:
            self._last_update_time = now
            try:
                self.callback(progress)
            except Exception as exc:
                logger.warning("Progress callback failed: %s", exc)

    def complete(self, file_path: Path) -> None:
        """Mark the download as successfully complete.

        Args:
            file_path: Path to the final downloaded file.
        """
        self._completed = True

        progress = ProgressUpdate(
            percentage=100.0,
            downloaded_bytes=file_path.stat().st_size if file_path.exists() else 0,
            total_bytes=file_path.stat().st_size if file_path.exists() else None,
            speed=None,
            eta=0,
            phase="complete",
        )

        self._latest = progress
        self._history.append(progress)

        if self.callback:
            try:
                self.callback(progress)
            except Exception as exc:
                logger.warning("Progress callback failed on complete: %s", exc)

        logger.info(
            "Download complete for job %s: %s (%.1fs elapsed)",
            self.job_id,
            file_path,
            self.elapsed_seconds,
        )

    def error(self, message: str) -> None:
        """Mark the download as failed with an error.

        Args:
            message: Error description.
        """
        self._completed = True

        progress = ProgressUpdate(
            percentage=self._latest.percentage if self._latest else None,
            downloaded_bytes=self._latest.downloaded_bytes if self._latest else 0,
            total_bytes=self._latest.total_bytes if self._latest else None,
            speed=None,
            eta=None,
            phase="error",
            error_message=message,
        )

        self._latest = progress
        self._history.append(progress)

        if self.callback:
            try:
                self.callback(progress)
            except Exception as exc:
                logger.warning("Progress callback failed on error: %s", exc)

        logger.error(
            "Download failed for job %s: %s (%.1fs elapsed)",
            self.job_id,
            message,
            self.elapsed_seconds,
        )
