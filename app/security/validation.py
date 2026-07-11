"""Input validation and sanitization for the Slaptastic API.

Provides URL allowlisting for music platforms, input sanitization to strip
dangerous characters, and path traversal prevention for file operations.
"""

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from app.config import get_settings

# Allowed music platform domains
ALLOWED_DOMAINS: set[str] = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "open.spotify.com",
    "music.apple.com",
}

# Maximum input lengths
MAX_URL_LENGTH = 2048
MAX_INPUT_LENGTH = 1024

# Control character pattern (C0 and C1 control chars, excluding common whitespace)
_CONTROL_CHAR_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def validate_music_url(url: str) -> str:
    """Validate that a URL points to an allowed music platform.

    Checks the URL against a set of known music platform domains to prevent
    abuse of the download pipeline for non-music content.

    Args:
        url: The URL to validate.

    Returns:
        The validated (stripped) URL.

    Raises:
        ValidationError: If the URL is empty, too long, malformed, or points
            to a domain not in the allowlist.
    """
    if not url or not url.strip():
        raise ValidationError("URL cannot be empty")

    url = url.strip()

    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(
            f"URL exceeds maximum length of {MAX_URL_LENGTH} characters"
        )

    try:
        parsed = urlparse(url)
    except Exception:
        raise ValidationError("Malformed URL") from None

    if parsed.scheme not in ("http", "https"):
        raise ValidationError(
            f"Invalid URL scheme '{parsed.scheme}' -- only http and https are allowed"
        )

    if not parsed.netloc:
        raise ValidationError("URL must include a domain")

    # Extract the hostname (strip port if present)
    hostname = parsed.hostname
    if hostname is None:
        raise ValidationError("Could not parse hostname from URL")

    hostname = hostname.lower()

    if hostname not in ALLOWED_DOMAINS:
        raise ValidationError(
            f"Domain '{hostname}' is not an allowed music platform. "
            f"Allowed: youtube.com, youtu.be, open.spotify.com, music.apple.com"
        )

    return url


def sanitize_input(value: str, max_length: int = MAX_INPUT_LENGTH) -> str:
    """Sanitize a text input by removing control characters and limiting length.

    Strips:
    - Leading/trailing whitespace
    - C0 and C1 control characters (except tab, newline, carriage return)
    - Unicode direction override characters
    - Null bytes

    Normalizes Unicode to NFC form to prevent homograph-style issues.

    Args:
        value: The input string to sanitize.
        max_length: Maximum allowed length after sanitization.

    Returns:
        The sanitized string, truncated to max_length if necessary.
    """
    if not value:
        return ""

    # Normalize Unicode to NFC (canonical decomposition + composition)
    value = unicodedata.normalize("NFC", value)

    # Strip leading/trailing whitespace
    value = value.strip()

    # Remove control characters
    value = _CONTROL_CHAR_RE.sub("", value)

    # Remove Unicode direction override characters (used in bidi attacks)
    direction_overrides = "‎‏‪‫‬‭‮⁦⁧⁨⁩"
    for char in direction_overrides:
        value = value.replace(char, "")

    # Truncate to max length
    if len(value) > max_length:
        value = value[:max_length]

    return value


def validate_safe_path(requested_path: str, base_directory: Path | None = None) -> Path:
    """Validate that a file path does not escape the allowed base directory.

    Prevents path traversal attacks by resolving the path and confirming it
    remains within the configured music base directory.

    Args:
        requested_path: The path to validate (may be relative or absolute).
        base_directory: The allowed base directory. Defaults to the configured
            music_base_path from settings.

    Returns:
        The resolved, validated Path object.

    Raises:
        ValidationError: If the path attempts to escape the base directory,
            contains null bytes, or is otherwise unsafe.
    """
    if not requested_path:
        raise ValidationError("Path cannot be empty")

    # Check for null bytes (common injection technique)
    if "\x00" in requested_path:
        raise ValidationError("Path contains null bytes")

    if base_directory is None:
        settings = get_settings()
        base_directory = settings.music_base_path

    # Resolve both paths to absolute canonical form
    base_resolved = base_directory.resolve()
    try:
        requested_resolved = (base_directory / requested_path).resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Invalid path: {exc}") from exc

    # Ensure the resolved path is within the base directory
    try:
        requested_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValidationError(
            "Path traversal detected -- path escapes the allowed base directory"
        ) from None

    return requested_resolved
