"""Command handler for @slaptastic bot commands.

Parses commands from incoming messages and dispatches them to the
appropriate job actions. Returns formatted response strings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.mattermost.formatter import MessageFormatter

logger = logging.getLogger(__name__)

# Valid commands the bot recognizes
VALID_COMMANDS = frozenset({"status", "candidates", "add", "approve", "cancel", "retry"})

# Commands handled by other services (WhatsApp worker) — don't reject these
PASSTHROUGH_COMMANDS = frozenset({"whatsapp"})


class JobService(Protocol):
    """Protocol defining the job service interface expected by the command handler.

    Implementations must provide these async methods for managing music
    library jobs (search, download, approval workflow).
    """

    async def get_status(self, channel_id: str, user_id: str) -> dict[str, Any]:
        """Get the current job status for the user/channel.

        Returns a dict with keys: state, track_title, artist, error (optional).
        """
        ...

    async def get_candidates(
        self, channel_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        """Get the list of search candidates for the active job.

        Returns a list of dicts with keys: number, title, artist, album,
        score, duration.
        """
        ...

    async def select_candidate(
        self, channel_id: str, user_id: str, candidate_number: int
    ) -> dict[str, Any]:
        """Select a candidate by number for the active job.

        Returns a dict with keys: title, artist, album, state.
        """
        ...

    async def approve(self, channel_id: str, user_id: str) -> dict[str, Any]:
        """Approve the selected candidate for download.

        Returns a dict with keys: title, artist, state.
        """
        ...

    async def cancel(self, channel_id: str, user_id: str) -> dict[str, Any]:
        """Cancel the active job.

        Returns a dict with keys: title, state.
        """
        ...

    async def retry(self, channel_id: str, user_id: str) -> dict[str, Any]:
        """Retry a failed job.

        Returns a dict with keys: title, artist, state.
        """
        ...


@dataclass
class ParsedCommand:
    """A parsed @slaptastic command."""

    name: str  # The command name (lowercase)
    args: str | None  # Optional arguments string
    raw: str  # The full original message text


def parse_command(command_name: str | None, command_args: str | None, raw_message: str = "") -> ParsedCommand | None:
    """Parse a command name and args into a ParsedCommand.

    Args:
        command_name: The command name extracted from the message (already lowercase).
        command_args: The arguments string (may be None).
        raw_message: The full original message text.

    Returns:
        A ParsedCommand if valid, or None if the command is not recognized.
    """
    if not command_name:
        return None

    name = command_name.lower()
    if name in PASSTHROUGH_COMMANDS:
        return None  # Handled by another service (WhatsApp worker)
    if name not in VALID_COMMANDS:
        return None

    return ParsedCommand(name=name, args=command_args, raw=raw_message)


class CommandHandler:
    """Handles @slaptastic commands by dispatching to the job service.

    Usage:
        handler = CommandHandler(job_service=my_service)
        response = await handler.handle(parsed_command, channel_id, user_id)
        # response is a formatted string ready to post to Mattermost

    If no job_service is provided, commands that require it will return
    a placeholder message. This allows the handler to be instantiated
    during startup before the full pipeline is wired.
    """

    def __init__(
        self,
        job_service: JobService | None = None,
        client: Any | None = None,
    ) -> None:
        self._jobs = job_service
        self._client = client
        self._formatter = MessageFormatter()

    async def handle(
        self, message_or_command: Any, channel_id: str = "", user_id: str = ""
    ) -> str:
        """Dispatch a parsed command and return the formatted response.

        Can accept either a ParsedCommand (with channel_id and user_id) or
        an IncomingMessage directly (for use from the WebSocket listener).

        Args:
            message_or_command: Either a ParsedCommand or IncomingMessage.
            channel_id: Channel where the command was issued (for ParsedCommand).
            user_id: User who issued the command (for ParsedCommand).

        Returns:
            A formatted message string for Mattermost.
        """
        # Support being called with IncomingMessage directly
        if hasattr(message_or_command, "command"):
            # It's an IncomingMessage
            msg = message_or_command
            parsed = parse_command(msg.command, msg.command_args, msg.message)
            if parsed is None:
                return self._formatter.format_error(
                    f"Unknown command. Valid commands: {', '.join(sorted(VALID_COMMANDS))}"
                )
            command = parsed
            channel_id = msg.channel_id
            user_id = msg.user_id
        else:
            command = message_or_command

        try:
            handler_method = getattr(self, f"_handle_{command.name}", None)
            if handler_method is None:
                return self._formatter.format_error(
                    f"Unknown command: `{command.name}`"
                )
            return await handler_method(command, channel_id, user_id)  # type: ignore[no-any-return]
        except Exception as exc:
            logger.exception("Error handling command '%s'", command.name)
            return self._formatter.format_error(
                f"Something went wrong processing `{command.name}`: {exc}"
            )

    async def _handle_status(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'status' command: show current job status."""
        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        status = await self._jobs.get_status(channel_id, user_id)
        return self._formatter.format_status(status)

    async def _handle_candidates(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'candidates' command: list search candidates with scores."""
        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        candidates = await self._jobs.get_candidates(channel_id, user_id)
        if not candidates:
            return self._formatter.format_error(
                "No candidates found. Submit a music link first."
            )
        return self._formatter.format_candidates(candidates)

    async def _handle_add(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'add N' command: select a candidate by number."""
        if not command.args:
            return self._formatter.format_error(
                "Please specify a candidate number: `@slaptastic add 1`"
            )

        try:
            candidate_number = int(command.args.strip())
        except ValueError:
            return self._formatter.format_error(
                f"Invalid candidate number: `{command.args}`. "
                "Use a number like: `@slaptastic add 1`"
            )

        if candidate_number < 1:
            return self._formatter.format_error(
                "Candidate number must be 1 or greater."
            )

        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        result = await self._jobs.select_candidate(channel_id, user_id, candidate_number)
        return self._formatter.format_candidate_selected(result)

    async def _handle_approve(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'approve' command: approve for download."""
        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        result = await self._jobs.approve(channel_id, user_id)
        return self._formatter.format_approved(result)

    async def _handle_cancel(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'cancel' command: cancel the active job."""
        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        result = await self._jobs.cancel(channel_id, user_id)
        return self._formatter.format_cancelled(result)

    async def _handle_retry(
        self, command: ParsedCommand, channel_id: str, user_id: str
    ) -> str:
        """Handle the 'retry' command: retry a failed job."""
        if self._jobs is None:
            return self._formatter.format_error("No job service configured")
        result = await self._jobs.retry(channel_id, user_id)
        return self._formatter.format_retry(result)
