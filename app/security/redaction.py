"""Secret redaction for log messages and debug output.

Detects and redacts sensitive patterns (API keys, bearer tokens, passwords)
from strings before they reach log output, preventing accidental secret
exposure in logs, error messages, and monitoring systems.
"""

import re
from collections.abc import Sequence
from typing import Any

# Placeholder for redacted values
REDACTED = "[REDACTED]"

# Compiled patterns for secrets detection
# Each tuple is (pattern_name, compiled_regex, replacement)
_REDACTION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Bearer tokens in Authorization headers
    (
        "bearer_token",
        re.compile(r"(Bearer\s+)\S+", re.IGNORECASE),
        rf"\1{REDACTED}",
    ),
    # Generic API key patterns (key=value, api_key=value, apikey=value)
    (
        "api_key_param",
        re.compile(
            r"((?:api[_-]?key|apikey|token|secret|password|auth)[=:]\s*)([^\s&,;\"']+)",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # Passwords in URLs (scheme://user:password@host)
    (
        "url_password",
        re.compile(r"(://[^:]+:)([^@]+)(@)"),
        rf"\1{REDACTED}\3",
    ),
    # JSON-style key-value patterns for tokens/secrets
    (
        "json_secret",
        re.compile(
            r'("(?:token|secret|password|api_key|apikey|auth_token|access_token|'
            r'refresh_token|client_secret|private_key)":\s*")([^"]+)(")',
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}\3",
    ),
    # Header-style patterns (X-API-Key: value, Authorization: value)
    (
        "header_secret",
        re.compile(
            r"((?:X-API-Key|Authorization|X-Auth-Token|X-Secret)[:\s]+)(\S+)",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # AWS-style access keys (AKIA...)
    (
        "aws_key",
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        REDACTED,
    ),
    # Long hex strings that look like tokens (32+ chars)
    (
        "hex_token",
        re.compile(r"\b([0-9a-fA-F]{32,})\b"),
        REDACTED,
    ),
]


def redact_secrets(message: str) -> str:
    """Redact potential secrets from a log message or string.

    Applies multiple regex patterns to detect and replace sensitive values
    with a [REDACTED] placeholder. Patterns include:
    - Bearer tokens
    - API keys in query parameters or config
    - Passwords in URLs
    - JSON-encoded secrets
    - Authentication headers
    - AWS access keys
    - Long hex strings that look like tokens

    Args:
        message: The string to redact secrets from.

    Returns:
        The message with all detected secrets replaced by [REDACTED].
    """
    if not message:
        return message

    result = message
    for _name, pattern, replacement in _REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def redact_dict(data: dict, sensitive_keys: Sequence[str] | None = None) -> dict:
    """Redact values for known-sensitive keys in a dictionary.

    Useful for sanitizing configuration or request data before logging.

    Args:
        data: The dictionary to redact.
        sensitive_keys: Keys whose values should be redacted. Defaults to
            a standard set of secret-related key names.

    Returns:
        A new dictionary with sensitive values replaced by [REDACTED].
    """
    if sensitive_keys is None:
        sensitive_keys = (
            "token",
            "secret",
            "password",
            "api_key",
            "apikey",
            "auth_token",
            "access_token",
            "refresh_token",
            "client_secret",
            "private_key",
            "mattermost_token",
            "jellyfin_token",
            "admin_api_token",
            "spotify_client_secret",
            "apple_music_token",
        )

    sensitive_set = {k.lower() for k in sensitive_keys}
    result: dict[str, Any] = {}

    for key, value in data.items():
        if key.lower() in sensitive_set:
            result[key] = REDACTED
        elif isinstance(value, dict):
            result[key] = redact_dict(value, sensitive_keys)
        elif isinstance(value, str):
            result[key] = redact_secrets(value)
        else:
            result[key] = value

    return result
