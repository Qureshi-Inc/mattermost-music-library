"""Helpers around the scrobble_adds dedup table."""

from __future__ import annotations

from sqlalchemy import select

from app.database import async_session_factory
from app.models.scrobble import ScrobbleAdd


async def already_added(source_name: str, norm_key: str) -> bool:
    """True if this (source, track) has already been auto-added."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ScrobbleAdd.id).where(
                ScrobbleAdd.source_name == source_name,
                ScrobbleAdd.norm_key == norm_key,
            )
        )
        return result.first() is not None


async def mark_added(
    source_name: str, norm_key: str, artist: str, track: str, plays: int
) -> None:
    """Record that a (source, track) was auto-added so we never repeat it."""
    async with async_session_factory() as session:
        session.add(
            ScrobbleAdd(
                source_name=source_name,
                norm_key=norm_key,
                artist=artist,
                track=track,
                play_count=plays,
            )
        )
        await session.commit()


async def last_alert_at(source_name: str):
    """Return the datetime of the last health alert for a source, or None."""
    from app.models.scrobble import ScrobbleHealthAlert

    async with async_session_factory() as session:
        result = await session.execute(
            select(ScrobbleHealthAlert).where(
                ScrobbleHealthAlert.source_name == source_name
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        ts = row.updated_at or row.created_at
        # Normalize to aware UTC for safe comparison.
        if ts is not None and ts.tzinfo is None:
            from datetime import timezone

            ts = ts.replace(tzinfo=timezone.utc)
        return ts


async def record_alert(source_name: str) -> None:
    """Upsert the last-alerted timestamp for a source (bumps updated_at)."""
    from datetime import datetime, timezone

    from app.models.scrobble import ScrobbleHealthAlert

    async with async_session_factory() as session:
        result = await session.execute(
            select(ScrobbleHealthAlert).where(
                ScrobbleHealthAlert.source_name == source_name
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(ScrobbleHealthAlert(source_name=source_name))
        else:
            # Force updated_at to bump even though no other column changed.
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
