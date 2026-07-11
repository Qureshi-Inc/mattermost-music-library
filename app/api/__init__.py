"""Slaptastic API router package.

Exposes a top-level router that includes all versioned sub-routers.
The main application mounts this at /api, giving final paths like
/api/v1/jobs, /api/v1/tracks, etc.
"""

from fastapi import APIRouter

from app.api.router import v1_router

router = APIRouter()
router.include_router(v1_router)
