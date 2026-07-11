"""Authentication utilities for the Slaptastic API.

Provides admin token verification using constant-time comparison to prevent
timing attacks, and a FastAPI dependency for protecting endpoints.
"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

# HTTPBearer scheme extracts the token from "Authorization: Bearer <token>"
_bearer_scheme = HTTPBearer(auto_error=False)


def verify_admin_token(token: str) -> bool:
    """Verify an API token against the configured admin token.

    Uses hmac.compare_digest for constant-time comparison to prevent
    timing side-channel attacks.

    Args:
        token: The token string to verify.

    Returns:
        True if the token matches the configured ADMIN_API_TOKEN.
    """
    settings = get_settings()
    if not settings.admin_api_token:
        # If no admin token is configured, reject all requests
        return False
    return hmac.compare_digest(token.encode("utf-8"), settings.admin_api_token.encode("utf-8"))


async def require_admin(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
) -> str:
    """FastAPI dependency that enforces admin authentication.

    Extracts the Bearer token from the Authorization header and verifies it
    against the configured ADMIN_API_TOKEN.

    Returns:
        The verified token string (useful for logging/audit).

    Raises:
        HTTPException 401: If no Authorization header or Bearer token is provided.
        HTTPException 403: If the provided token is invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_admin_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired API token",
        )

    return credentials.credentials
