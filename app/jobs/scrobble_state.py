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
