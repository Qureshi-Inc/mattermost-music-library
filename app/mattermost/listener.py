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

    _recent_links: dict[str, float] = {}

    async def on_music_link(message: IncomingMessage) -> None:
        """Handle a detected music link in the channel.

        Only processes the first music URL per message to avoid duplicates.
        """
        import time
        from app.database import async_session_factory
        from app.jobs.queue import JobQueue
        from app.models.job import SourcePlatform

        if not message.music_urls:
            return

        # Only process the first URL per message
        url = message.music_urls[0]

        # Dedup: ignore same URL from same user within 60 seconds
        dedup_key = f"{message.user_id}:{url}"
        now = time.time()
        if dedup_key in _recent_links and now - _recent_links[dedup_key] < 60:
            logger.info("Ignoring duplicate link from %s (within 60s)", message.username)
            return
        _recent_links[dedup_key] = now
        # Clean old entries
        for k in list(_recent_links):
            if now - _recent_links[k] > 120:
                del _recent_links[k]

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
                    # Still add to user's playlist even if song already exists
                    if pipeline:
                        await pipeline._add_to_user_playlist_by_name(
                            track.title, track.artist, message.user_id
                        )
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
                            mattermost_post_id=None,
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

            # Build links
            links = ""
            if track.spotify_id:
                links += f"[:spotify:](https://open.spotify.com/track/{track.spotify_id})"
            if track.apple_music_id:
                if links:
                    links += " "
                links += f"[:applem:](https://music.apple.com/track/{track.apple_music_id})"

            results.append({"title": track.title, "artist": track.artist, "status": status, "links": links})

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
        summary += "| # | Song | Artist | Links | Status |\n|---|------|--------|-------|--------|\n"
        for i, r in enumerate(results):
            summary += f"| {i+1} | {r['title'][:30]} | {r['artist'][:25]} | {r['links']} | {r['status']} |\n"

        if failed > 0:
            summary += f"\n_{failed} song(s) could not be found or downloaded._"

        await update_status(summary)
        logger.info("Playlist import complete: %s (%d added, %d exists, %d failed)", playlist.name, added, exists, failed)

    async def on_command(message: IncomingMessage) -> None:
        """Handle an @slaptastic command."""
        # Handle bare @slaptastic in a thread — scan for music links
        if message.command == "_thread_scan":
            await _handle_thread_scan(message, client)
            return

        response = await command_handler.handle(message)
        if response:
            thread_id = message.root_id or message.post_id
            await client.reply_in_thread(
                channel_id=message.channel_id,
                root_id=thread_id,
                message=response,
            )

    async def _handle_thread_scan(message: IncomingMessage, mm_client: MattermostClient) -> None:
        """Scan a thread for music links and process the first one found."""
        from app.database import async_session_factory
        from app.jobs.queue import JobQueue
        from app.models.job import SourcePlatform

        thread_id = message.root_id
        if not thread_id:
            return

        # Fetch the thread
        try:
            if not mm_client._session:
                mm_client._session = __import__("aiohttp").ClientSession()
            url = f"{mm_client.api_url}/posts/{thread_id}/thread"
            async with mm_client._session.get(url, headers=mm_client._headers) as resp:
                if resp.status != 200:
                    return
                thread_data = await resp.json()
        except Exception as e:
            logger.error("Failed to fetch thread: %s", e)
            return

        # Get the root message (the one the thread was created under)
        posts = thread_data.get("posts", {})
        root_post = posts.get(thread_id, {})
        root_message = root_post.get("message", "")
        # Use the original poster's user_id for playlist attribution
        original_user_id = root_post.get("user_id", message.user_id)

        # Check root message for music/playlist URLs
        from app.mattermost.client import MUSIC_URL_COMBINED, PLAYLIST_URL_COMBINED
        playlist_urls = PLAYLIST_URL_COMBINED.findall(root_message)
        playlist_urls = [u.rstrip(")],;!?. ") for u in playlist_urls]

        music_urls = MUSIC_URL_COMBINED.findall(root_message)
        music_url = music_urls[0].rstrip(")],;!?. ") if music_urls else None

        # If root message has a playlist link, process as playlist
        if playlist_urls:
            from app.resolvers.playlist import is_playlist_url
            playlist_url = playlist_urls[0]
            if is_playlist_url(playlist_url):
                playlist_message = IncomingMessage(
                    post_id=message.post_id,
                    channel_id=message.channel_id,
                    user_id=original_user_id,
                    username=message.username,
                    message=root_message,
                    root_id=thread_id,
                    playlist_urls=[playlist_url],
                )
                await on_playlist(playlist_message)
                return

        if not music_url:
            await mm_client.reply_in_thread(
                channel_id=message.channel_id,
                root_id=thread_id,
                message="I couldn't find a music link in this thread.",
            )
            return

        # Determine platform
        platform = SourcePlatform.UNKNOWN
        if "youtube.com" in music_url or "youtu.be" in music_url:
            platform = SourcePlatform.YOUTUBE
        elif "spotify.com" in music_url:
            platform = SourcePlatform.SPOTIFY
        elif "music.apple.com" in music_url:
            platform = SourcePlatform.APPLE_MUSIC

        # Create job — attribute to the original poster
        async with async_session_factory() as session:
            queue = JobQueue(session)
            job = await queue.create_job(
                url=music_url,
                source_platform=platform,
                mattermost_post_id=message.post_id,
                mattermost_channel_id=message.channel_id,
                requester_user_id=original_user_id,
            )
            await session.commit()

        logger.info("Thread scan created job", extra={"job_id": str(job.id), "url": music_url})

        # Post status
        result = await mm_client.reply_in_thread(
            channel_id=message.channel_id,
            root_id=thread_id,
            message="⏳ Processing...",
        )

        if result and result.get("id"):
            from app.jobs.pipeline import get_pipeline
            pipeline = get_pipeline()
            if pipeline:
                pipeline._status_post_ids[str(job.id)] = result["id"]

    client.on_music_link(on_music_link)
    client.on_playlist(on_playlist)
    client.on_command(on_command)

    logger.info(
        "Starting Mattermost WebSocket listener",
        extra={"channel_id": settings.mattermost_channel},
    )

    await client.start()
