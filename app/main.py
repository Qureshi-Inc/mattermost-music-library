"""FastAPI application entry point for Slaptastic music library bot."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import get_settings
from app.database import check_db_connectivity, dispose_engine, init_db
from app.logging_config import get_logger, setup_logging

logger = get_logger(__name__)

# Background task references
_mattermost_ws_task: asyncio.Task | None = None
_pipeline_task: asyncio.Task | None = None
_pipeline_instance = None


async def _start_mattermost_listener() -> None:
    """Start the Mattermost WebSocket listener as a background task.

    Imports the listener lazily to avoid circular imports and to allow
    the application to start even if the mattermost module has issues.
    """
    global _mattermost_ws_task
    settings = get_settings()

    if not settings.mattermost_token:
        logger.warning(
            "Mattermost token not configured -- WebSocket listener will not start"
        )
        return

    try:
        from app.mattermost.listener import run_websocket_listener

        _mattermost_ws_task = asyncio.create_task(
            run_websocket_listener(),
            name="mattermost-ws-listener",
        )
        logger.info("Mattermost WebSocket listener started")
    except ImportError:
        logger.warning(
            "Mattermost listener module not available -- skipping WebSocket startup"
        )
    except Exception as exc:
        logger.error(
            "Failed to start Mattermost WebSocket listener",
            exc_info=exc,
        )


async def _start_pipeline() -> None:
    """Start the job processing pipeline as a background task."""
    global _pipeline_instance
    try:
        from app.jobs.pipeline import JobPipeline
        from app.jobs.queue import JobQueue
        from app.mattermost.client import MattermostClient, MattermostConfig

        settings = get_settings()
        queue = JobQueue()

        # Create a Mattermost client for the pipeline to post status updates
        mm_client = None
        if settings.mattermost_token:
            mm_config = MattermostConfig(
                url=settings.mattermost_url,
                bot_token=settings.mattermost_token,
                channel_id=settings.mattermost_channel,
                bot_username=settings.bot_username,
            )
            mm_client = MattermostClient(mm_config)

        _pipeline_instance = JobPipeline(queue=queue, mattermost_client=mm_client)
        await _pipeline_instance.start()
        logger.info("Job pipeline started")
    except Exception as exc:
        logger.error("Failed to start job pipeline", exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown logic."""
    # --- Startup ---
    setup_logging()
    logger.info(
        "Slaptastic starting up",
        extra={"version": __version__},
    )

    # Initialize database tables
    await init_db()
    logger.info("Database initialized")

    # Start Mattermost WebSocket listener
    await _start_mattermost_listener()

    # Start job pipeline
    await _start_pipeline()

    yield

    # --- Shutdown ---
    logger.info("Slaptastic shutting down")

    # Stop the job pipeline
    if _pipeline_instance is not None:
        await _pipeline_instance.stop()
        logger.info("Job pipeline stopped")

    # Cancel the WebSocket listener task if running
    if _mattermost_ws_task is not None and not _mattermost_ws_task.done():
        _mattermost_ws_task.cancel()
        with suppress(asyncio.CancelledError):
            await _mattermost_ws_task
        logger.info("Mattermost WebSocket listener stopped")

    # Dispose of database engine
    await dispose_engine()
    logger.info("Database connections closed")


app = FastAPI(
    title="Slaptastic",
    description="Music library bot for Mattermost powered by Jellyfin",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint. Returns healthy if the service is running."""
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness check endpoint. Verifies database connectivity."""
    db_ok = await check_db_connectivity()

    if db_ok:
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "database": "connected"},
        )

    return JSONResponse(
        status_code=503,
        content={"status": "not ready", "database": "disconnected"},
    )


# Include the API router
try:
    from app.api import router as api_router

    app.include_router(api_router, prefix="/api")
except ImportError:
    logger.warning("API router not available -- /api routes will not be registered")


# Serve the dashboard
_static_dir = Path(__file__).parent / "static" / "dashboard"
if _static_dir.exists():

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/dashboard/", include_in_schema=False)
    async def serve_dashboard() -> FileResponse:
        """Serve the Slapshare music leaderboard dashboard."""
        return FileResponse(
            str(_static_dir / "index.html"),
            media_type="text/html",
        )

    # Mount static directory for any additional assets (CSS/JS/images)
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=str(_static_dir)),
        name="dashboard-assets",
    )
else:
    logger.warning("Dashboard static directory not found -- /dashboard will not be available")
