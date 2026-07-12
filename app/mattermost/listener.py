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
    client.on_command(on_command)

    logger.info(
        "Starting Mattermost WebSocket listener",
        extra={"channel_id": settings.mattermost_channel},
    )

    await client.start()
