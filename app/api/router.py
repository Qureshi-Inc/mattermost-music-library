"""Main API router combining all endpoint routers under /api/v1 prefix.

This module assembles the versioned API by including all sub-routers
(jobs, tracks) under a common /v1 prefix. Additional routers can be
added here as new API modules are developed.
"""

from fastapi import APIRouter

from app.api.jobs import router as jobs_router
from app.api.tracks import router as tracks_router

# The v1 router aggregates all endpoint groups
v1_router = APIRouter(prefix="/v1")
v1_router.include_router(jobs_router)
v1_router.include_router(tracks_router)
