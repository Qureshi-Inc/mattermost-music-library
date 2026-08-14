"""Async SQLAlchemy database engine and session management.

Uses aiosqlite for local development (SQLite) with a connection pattern that is
compatible with async PostgreSQL (asyncpg) for production use -- just swap the
DB URL in config.
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


settings = get_settings()

engine = create_async_engine(
    settings.db_url,
    echo=False,
    # SQLite-specific: allow same connection across threads (safe with async)
    connect_args={"check_same_thread": False} if "sqlite" in settings.db_url else {},
    # Pool settings suitable for both SQLite and PostgreSQL
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    The session is automatically closed when the request completes.
    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize the database by creating all tables.

    Call this during application startup (lifespan). It creates tables
    defined by any model that inherits from Base.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Lightweight idempotent migrations for columns added after a table already
    # exists (create_all won't ALTER existing tables). Safe to run every start.
    _added_columns = [
        ("jobs", "status_post_id", "VARCHAR(64)"),
    ]
    async with async_session_factory() as session:
        for table, col, coltype in _added_columns:
            try:
                res = await session.execute(text(f"PRAGMA table_info({table})"))
                cols = {row[1] for row in res}
                if col not in cols:
                    await session.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                    )
                    await session.commit()
            except Exception:
                # Non-SQLite backends / race: ignore; model create_all covers new DBs.
                await session.rollback()


async def check_db_connectivity() -> bool:
    """Check whether the database is reachable.

    Returns:
        True if a simple query succeeds, False otherwise.
    """
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    """Dispose of the async engine connection pool.

    Call during application shutdown to cleanly release connections.
    """
    await engine.dispose()
