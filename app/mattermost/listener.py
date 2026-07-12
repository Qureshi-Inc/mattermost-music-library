"""Mattermost WebSocket listener entry point.

This module provides the top-level coroutine that main.py starts as a background
task. It wires together the MattermostClient, CommandHandler, and JobPipeline.
"""

import logging

from app.config import get_settings
from app.mattermost.client import IncomingMessage, MattermostClient, MattermostConfig
from app.mattermost.commands import CommandHandler

logger = logging.getLogger(__name__)


async def run_websocket_listener() -> None:
    """Run the Mattermost WebSocket listener.

    This is an async coroutine that runs indefinitely, connecting to
    the Mattermost WebSocket and dispatching events to the job pipeline.
    """
    settings = get_settings()

    config = MattermostConfig(
        url=settings.mattermost_url,
        bot_token=settings.mattermost_token,
        channel_id=settings.mattermost_channel,
        bot_username=settings.bot_username,
    )

    client = MattermostClient(config)
    command_handler = CommandHandler(client=client)

    async def on_music_link(message: IncomingMessage) -> None:
        """Handle a detected music link in the channel.

        Only processes the first music URL per message to avoid duplicates.
        """
        from app.database import async_session_factory
        from app.jobs.queue import JobQueue
        from app.models.job import SourcePlatform

        if not message.music_urls:
            return

        # Only process the first URL per message
        url = message.music_urls[0]

        # Determine source platform from URL
        platform = SourcePlatform.UNKNOWN
        if "youtube.com" in url or "youtu.be" in url:
            platform = SourcePlatform.YOUTUBE
        elif "spotify.com" in url:
            platform = SourcePlatform.SPOTIFY
        elif "music.apple.com" in url:
            platform = SourcePlatform.APPLE_MUSIC

        # Create a job for this link
        async with async_session_factory() as session:
            queue = JobQueue(session)
            job = await queue.create_job(
                url=url,
                source_platform=platform,
                mattermost_post_id=message.post_id,
                mattermost_channel_id=message.channel_id,
                requester_user_id=message.user_id,
            )
            await session.commit()

        logger.info(
            "Created job for music link",
            extra={
                "job_id": str(job.id),
                "url": url,
                "platform": platform.value,
                "user": message.username,
            },
        )

        # Reply in thread acknowledging the link
        thread_id = message.root_id or message.post_id
        result = await client.reply_in_thread(
            channel_id=message.channel_id,
            root_id=thread_id,
            message="⏳ Processing...",
        )

        # Store the reply post ID so the pipeline can edit it
        if result and result.get("id"):
            from app.jobs.pipeline import get_pipeline
            pipeline = get_pipeline()
            if pipeline:
                job_key = str(job.id)
                pipeline._status_post_ids[job_key] = result["id"]
                logger.info("Stored status post_id=%s for job_key=%s", result["id"], job_key)
            else:
                logger.warning("Pipeline not available yet, status post won't be editable")

    async def on_playlist(message: IncomingMessage) -> None:
        """Handle a detected playlist link in the channel."""
        import asyncio
        from app.database import async_session_factory
        from app.jobs.queue import JobQueue
        from app.models.job import SourcePlatform
        from app.resolvers.playlist import resolve_playlist

        if not message.playlist_urls:
            return

        url = message.playlist_urls[0]
        thread_id = message.root_id or message.post_id

        # Post initial status message
        result = await client.reply_in_thread(
            channel_id=message.channel_id,
            root_id=thread_id,
            message="⏳ Loading playlist...",
        )
        status_post_id = result.get("id") if result else None

        async def update_status(msg: str) -> None:
            if status_post_id:
                try:
                    await client.update_post(status_post_id, msg)
                except Exception:
                    pass

        # Resolve the playlist
        playlist = await resolve_playlist(url)
        if not playlist or not playlist.tracks:
            await update_status("❌ Could not load playlist tracks. Check if the playlist is public.")
            return

        total = len(playlist.tracks)
        await update_status(f"⏳ Importing **{playlist.name}** (0/{total})...")

        # Process each track
        results: list[dict] = []
        from app.jobs.pipeline import get_pipeline
        pipeline = get_pipeline()

        for i, track in enumerate(playlist.tracks):
            status = "❌ Failed"
            try:
                # Check duplicate
                if pipeline and await pipeline._check_duplicate_by_name(track.title, track.artist):
                    status = "ℹ️ Already exists"
                else:
                    # Create a job and process it
                    track_url = f"https://open.spotify.com/track/{track.spotify_id}" if track.spotify_id else ""
                    if not track_url and track.apple_music_id:
                        track_url = f"https://music.apple.com/track/{track.apple_music_id}"

                    platform = SourcePlatform.SPOTIFY if track.spotify_id else SourcePlatform.APPLE_MUSIC

                    async with async_session_factory() as session:
                        queue = JobQueue(session)
                        job = await queue.create_job(
                            url=track_url or f"search:{track.artist} - {track.title}",
                            source_platform=platform,
                            mattermost_post_id=message.post_id,
                            mattermost_channel_id=message.channel_id,
                            requester_user_id=message.user_id,
                        )
                        await session.commit()

                    # Process inline (don't wait for pipeline poll)
                    if pipeline:
                        await pipeline.process_job(job.id)

                        # Check if it completed
                        updated_job = await pipeline.queue.get_job(job.id)
                        if updated_job and updated_job.status.value == "complete":
                            status = "✅ Added"
                        elif updated_job and "duplicate" in (updated_job.status.value or ""):
                            status = "ℹ️ Already exists"
                        else:
                            status = "❌ Failed"
                    else:
                        status = "⏳ Queued"
            except Exception as e:
                logger.error("Playlist track failed: %s - %s: %s", track.artist, track.title, e)
                status = "❌ Failed"

            results.append({"title": track.title, "artist": track.artist, "status": status})

            # Update progress every 2 tracks
            if (i + 1) % 2 == 0 or (i + 1) == total:
                completed = sum(1 for r in results if "Added" in r["status"] or "exists" in r["status"])
                await update_status(f"⏳ Importing **{playlist.name}** ({i + 1}/{total})...\n\n{completed} successful so far")

            # Small delay to avoid rate limits
            await asyncio.sleep(1)

        # Final summary
        added = sum(1 for r in results if "Added" in r["status"])
        exists = sum(1 for r in results if "exists" in r["status"])
        failed = sum(1 for r in results if "Failed" in r["status"])

        summary = f"✅ Playlist **{playlist.name}** imported ({added + exists}/{total} songs)\n\n"
        summary += "| # | Song | Artist | Status |\n|---|------|--------|--------|\n"
        for i, r in enumerate(results):
            summary += f"| {i+1} | {r['title'][:30]} | {r['artist'][:25]} | {r['status']} |\n"

        if failed > 0:
            summary += f"\n_{failed} song(s) could not be found or downloaded._"

        await update_status(summary)
        logger.info("Playlist import complete: %s (%d added, %d exists, %d failed)", playlist.name, added, exists, failed)

    async def on_command(message: IncomingMessage) -> None:
        """Handle an @slaptastic command."""
        response = await command_handler.handle(message)
        if response:
            thread_id = message.root_id or message.post_id
            await client.reply_in_thread(
                channel_id=message.channel_id,
                root_id=thread_id,
                message=response,
            )

    client.on_music_link(on_music_link)
    client.on_playlist(on_playlist)
    client.on_command(on_command)

    logger.info(
        "Starting Mattermost WebSocket listener",
        extra={"channel_id": settings.mattermost_channel},
    )

    await client.start()
