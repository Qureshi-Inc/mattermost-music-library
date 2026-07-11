"""Shared API dependencies for dependency injection.

Provides reusable FastAPI dependencies for database sessions and
authentication that are used across all API endpoint modules.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db as _get_db
from app.security.auth import require_admin as _require_admin

# Type aliases for cleaner endpoint signatures
DbSession = Annotated[AsyncSession, Depends(_get_db)]
AdminToken = Annotated[str, Depends(_require_admin)]
