"""Message formatter for Slaptastic Mattermost responses.

Formats job status updates, candidate lists, error messages, and
success messages into Mattermost-compatible markdown strings.
"""

from __future__ import annotations

from typing import Any

# Emoji mapping for job states
STATE_EMOJI: dict[str, str] = {
    "searching": ":mag:",
    "candidates_ready": ":ballot_box_with_check:",
    "selected": ":point_right:",
    "approved": ":white_check_mark:",
    "downloading": ":arrow_down:",
    "processing": ":gear:",
    "importing": ":inbox_tray:",
    "complete": ":tada:",
    "failed": ":x:",
    "cancelled": ":no_entry_sign:",
    "idle": ":zzz:",
}

# Human-readable state labels
STATE_LABELS: dict[str, str] = {
    "searching": "Searching for matches",
    "candidates_ready": "Candidates ready for review",
    "selected": "Candidate selected, awaiting approval",
    "approved": "Approved, queued for download",
    "downloading": "Downloading",
    "processing": "Processing audio",
    "importing": "Importing to library",
    "complete": "Complete",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "idle": "No active job",
}


class MessageFormatter:
    """Formats messages for posting to Mattermost.

    All methods return Mattermost-flavored markdown strings ready to be
    posted via the client.
    """

    def format_status(self, status: dict[str, Any]) -> str:
        """Format a job status update.

        Args:
            status: Dict with keys: state, track_title (optional),
                    artist (optional), error (optional).

        Returns:
            Formatted status message with emoji.
        """
        state = status.get("state", "idle")
        emoji = STATE_EMOJI.get(state, ":question:")
        label = STATE_LABELS.get(state, state.replace("_", " ").title())

        lines = [f"{emoji} **Status:** {label}"]

        track_title = status.get("track_title")
        artist = status.get("artist")
        if track_title and artist:
            lines.append(f":musical_note: **Track:** {artist} - {track_title}")
        elif track_title:
            lines.append(f":musical_note: **Track:** {track_title}")

        error = status.get("error")
        if error and state == "failed":
            lines.append(f"\n> :warning: **Error:** {error}")
            lines.append("\nUse `@slaptastic retry` to try again or `@slaptastic cancel` to abort.")

        return "\n".join(lines)

    def format_candidates(self, candidates: list[dict[str, Any]]) -> str:
        """Format a list of search candidates as a numbered table.

        Args:
            candidates: List of dicts with keys: number, title, artist,
                       album, score, duration.

        Returns:
            Formatted candidates table.
        """
        if not candidates:
            return ":mag: No candidates found."

        lines = [
            ":ballot_box_with_check: **Search Results:**",
            "",
            "| # | Score | Title | Artist | Album | Duration |",
            "|---|-------|-------|--------|-------|----------|",
        ]

        for candidate in candidates:
            number = candidate.get("number", "?")
            title = _escape_md(candidate.get("title", "Unknown"))
            artist = _escape_md(candidate.get("artist", "Unknown"))
            album = _escape_md(candidate.get("album", ""))
            score = candidate.get("score", 0)
            duration = _format_duration(candidate.get("duration", 0))

            # Format score as a percentage bar visual
            score_display = f"{score:.0f}%" if isinstance(score, (int, float)) else str(score)

            lines.append(
                f"| {number} | {score_display} | {title} | {artist} | {album} | {duration} |"
            )

        lines.append("")
        lines.append("Select a candidate with `@slaptastic add <number>`")

        return "\n".join(lines)

    def format_candidate_selected(self, result: dict[str, Any]) -> str:
        """Format a response for when a candidate is selected.

        Args:
            result: Dict with keys: title, artist, album, state.

        Returns:
            Formatted selection confirmation.
        """
        title = result.get("title", "Unknown")
        artist = result.get("artist", "Unknown")
        album = result.get("album", "")

        lines = [
            f":point_right: **Selected:** {artist} - {title}",
        ]
        if album:
            lines.append(f":cd: **Album:** {album}")
        lines.append("")
        lines.append("Use `@slaptastic approve` to download or `@slaptastic cancel` to abort.")

        return "\n".join(lines)

    def format_approved(self, result: dict[str, Any]) -> str:
        """Format a response for when a download is approved.

        Args:
            result: Dict with keys: title, artist, state.

        Returns:
            Formatted approval confirmation.
        """
        title = result.get("title", "Unknown")
        artist = result.get("artist", "Unknown")

        return (
            f":white_check_mark: **Approved!** Downloading {artist} - {title}\n\n"
            f"I'll let you know when it's ready in your library."
        )

    def format_cancelled(self, result: dict[str, Any]) -> str:
        """Format a response for when a job is cancelled.

        Args:
            result: Dict with keys: title, state.

        Returns:
            Formatted cancellation message.
        """
        title = result.get("title", "the current job")
        return f":no_entry_sign: **Cancelled:** {title}"

    def format_retry(self, result: dict[str, Any]) -> str:
        """Format a response for when a job is retried.

        Args:
            result: Dict with keys: title, artist, state.

        Returns:
            Formatted retry confirmation.
        """
        title = result.get("title", "Unknown")
        artist = result.get("artist", "Unknown")

        return (
            f":arrows_counterclockwise: **Retrying:** {artist} - {title}\n\n"
            f"I'll update you on progress."
        )

    def format_success(self, track_info: dict[str, Any]) -> str:
        """Format a success message when a track is added to the library.

        Args:
            track_info: Dict with keys: title, artist, album, duration,
                       library_url (optional).

        Returns:
            Formatted success message.
        """
        title = track_info.get("title", "Unknown")
        artist = track_info.get("artist", "Unknown")
        album = track_info.get("album", "")
        duration = _format_duration(track_info.get("duration", 0))
        library_url = track_info.get("library_url")

        lines = [
            ":tada: **Added to library!**",
            "",
            f":musical_note: **{artist} - {title}**",
        ]
        if album:
            lines.append(f":cd: Album: {album}")
        if duration:
            lines.append(f":stopwatch: Duration: {duration}")
        if library_url:
            lines.append(f":link: [Open in Jellyfin]({library_url})")

        return "\n".join(lines)

    def format_error(self, message: str) -> str:
        """Format an error message.

        Args:
            message: The error description.

        Returns:
            Formatted error message with emoji.
        """
        return f":x: **Error:** {message}"

    def format_music_link_received(self, url: str) -> str:
        """Format a confirmation that a music link was received and is being processed.

        Args:
            url: The detected music URL.

        Returns:
            Formatted acknowledgment message.
        """
        return (
            ":mag: **Got it!** Looking up that track...\n\n"
            "I'll search for matching candidates. "
            "Use `@slaptastic candidates` to see results when ready."
        )


def _escape_md(text: str) -> str:
    """Escape pipe characters in text for Mattermost table cells."""
    return text.replace("|", "\\|").replace("\n", " ")


def _format_duration(seconds: int | float) -> str:
    """Format a duration in seconds to MM:SS or H:MM:SS format."""
    if not seconds or seconds <= 0:
        return "--:--"

    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
