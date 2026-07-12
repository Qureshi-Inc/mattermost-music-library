"""Mattermost WebSocket client for Slaptastic bot.

Connects to a Mattermost server via WebSocket, listens for messages
in a configured channel, detects music links and @slaptastic commands,
and provides methods to post messages and reply in threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Music link URL patterns
MUSIC_URL_PATTERNS: list[re.Pattern[str]] = [
    # YouTube: youtube.com/watch?v=... or youtu.be/...
    re.compile(
        r"https?://(?:www\.)?youtube\.com/watch\?[^\s]*v=[A-Za-z0-9_-]+[^\s]*"
    ),
    re.compile(r"https?://youtu\.be/[A-Za-z0-9_-]+[^\s]*"),
    # Spotify: open.spotify.com/track/...
    re.compile(r"https?://open\.spotify\.com/track/[A-Za-z0-9]+[^\s]*"),
    # Apple Music: music.apple.com/...
    re.compile(r"https?://music\.apple\.com/[^\s]+"),
]

# Combined pattern to extract any music URL from text
MUSIC_URL_COMBINED = re.compile(
    r"("
    r"https?://(?:www\.)?youtube\.com/watch\?[^\s]*v=[A-Za-z0-9_-]+[^\s]*"
    r"|https?://youtu\.be/[A-Za-z0-9_-]+[^\s]*"
    r"|https?://open\.spotify\.com/track/[A-Za-z0-9]+[^\s]*"
    r"|https?://music\.apple\.com/[^\s]+"
    r")"
)

# Command pattern: @slaptastic <command> [args]
COMMAND_PATTERN = re.compile(
    r"@slaptastic\s+(\w+)(?:\s+(.+))?", re.IGNORECASE
)


@dataclass
class MattermostConfig:
    """Configuration for the Mattermost client."""

    url: str  # Base URL of the Mattermost server (https://mattermost.example.com)
    bot_token: str  # Bot authentication token
    channel_id: str  # Channel ID to listen on
    bot_username: str = "slaptastic"  # Bot username for command detection
    reconnect_base_delay: float = 1.0  # Base delay for exponential backoff (seconds)
    reconnect_max_delay: float = 60.0  # Maximum reconnect delay (seconds)
    reconnect_max_attempts: int = 0  # 0 = unlimited reconnection attempts


@dataclass
class IncomingMessage:
    """Represents a parsed incoming message from Mattermost."""

    post_id: str
    channel_id: str
    user_id: str
    username: str
    message: str
    root_id: str  # Thread root ID (empty string if not in a thread)
    music_urls: list[str] = field(default_factory=list)
    command: str | None = None
    command_args: str | None = None


# Type alias for event callbacks
EventCallback = Callable[[IncomingMessage], Coroutine[Any, Any, None]]


class MattermostClient:
    """Async Mattermost client using WebSocket for real-time events.

    Connects to the Mattermost WebSocket API, authenticates with a bot token,
    listens for new_post events, detects music links and commands, and provides
    methods to post messages and reply in threads.
    """

    def __init__(self, config: MattermostConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._seq = 0
        self._reconnect_attempt = 0
        self._on_music_link: EventCallback | None = None
        self._on_command: EventCallback | None = None

    @property
    def api_url(self) -> str:
        """Mattermost REST API v4 base URL."""
        return f"{self._config.url.rstrip('/')}/api/v4"

    @property
    def ws_url(self) -> str:
        """WebSocket URL derived from the Mattermost URL."""
        parsed = urlparse(self._config.url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.hostname}{':' + str(parsed.port) if parsed.port else ''}/api/v4/websocket"

    @property
    def _headers(self) -> dict[str, str]:
        """HTTP headers with bot authentication."""
        return {"Authorization": f"Bearer {self._config.bot_token}"}

    def on_music_link(self, callback: EventCallback) -> None:
        """Register a callback for when a music link is detected."""
        self._on_music_link = callback

    def on_command(self, callback: EventCallback) -> None:
        """Register a callback for when an @slaptastic command is detected."""
        self._on_command = callback

    async def start(self) -> None:
        """Start the client, connecting to WebSocket and listening for events."""
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("Mattermost client starting, connecting to %s", self.ws_url)
        await self._connect_loop()

    async def stop(self) -> None:
        """Gracefully stop the client."""
        self._running = False
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("Mattermost client stopped")

    async def post_message(
        self, channel_id: str, message: str, root_id: str = ""
    ) -> dict[str, Any]:
        """Post a message to a channel, optionally as a thread reply.

        Args:
            channel_id: The channel to post in.
            message: The message text (supports Mattermost markdown).
            root_id: If set, posts as a reply in the given thread.

        Returns:
            The created post object from the API.
        """
        if not self._session:
            self._session = aiohttp.ClientSession()

        payload: dict[str, Any] = {
            "channel_id": channel_id,
            "message": message,
        }
        if root_id:
            payload["root_id"] = root_id

        url = f"{self.api_url}/posts"
        async with self._session.post(
            url, json=payload, headers=self._headers
        ) as resp:
            if resp.status != 201:
                body = await resp.text()
                logger.error(
                    "Failed to post message (status=%d): %s", resp.status, body
                )
                raise RuntimeError(
                    f"Failed to post message: {resp.status} {body}"
                )
            return await resp.json()  # type: ignore[no-any-return]

    async def update_post(self, post_id: str, message: str) -> dict[str, Any]:
        """Update an existing post's message.

        Args:
            post_id: The ID of the post to update.
            message: The new message text.

        Returns:
            The updated post object from the API.
        """
        if not self._session:
            self._session = aiohttp.ClientSession()

        url = f"{self.api_url}/posts/{post_id}/patch"
        async with self._session.put(
            url, json={"message": message}, headers=self._headers
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(
                    "Failed to update post (status=%d): %s", resp.status, body
                )
                raise RuntimeError(
                    f"Failed to update post: {resp.status} {body}"
                )
            return await resp.json()  # type: ignore[no-any-return]

    async def reply_in_thread(
        self, channel_id: str, root_id: str, message: str
    ) -> dict[str, Any]:
        """Reply in a thread. Convenience wrapper around post_message.

        Args:
            channel_id: The channel containing the thread.
            root_id: The root post ID of the thread.
            message: The reply text.

        Returns:
            The created post object from the API.
        """
        return await self.post_message(channel_id, message, root_id=root_id)

    async def _connect_loop(self) -> None:
        """Main connection loop with exponential backoff reconnection."""
        while self._running:
            try:
                await self._connect_and_listen()
            except (
                aiohttp.ClientError,
                aiohttp.WSServerHandshakeError,
                ConnectionError,
                OSError,
            ) as exc:
                if not self._running:
                    break
                self._reconnect_attempt += 1
                if (
                    self._config.reconnect_max_attempts > 0
                    and self._reconnect_attempt > self._config.reconnect_max_attempts
                ):
                    logger.error(
                        "Max reconnection attempts (%d) reached, giving up",
                        self._config.reconnect_max_attempts,
                    )
                    break

                delay = min(
                    self._config.reconnect_base_delay
                    * (2 ** (self._reconnect_attempt - 1)),
                    self._config.reconnect_max_delay,
                )
                logger.warning(
                    "WebSocket connection lost (%s), reconnecting in %.1fs "
                    "(attempt %d)",
                    exc,
                    delay,
                    self._reconnect_attempt,
                )
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                logger.info("WebSocket listener cancelled")
                break

    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket, authenticate, and process events."""
        if not self._session:
            raise RuntimeError("Session not initialized")

        async with self._session.ws_connect(self.ws_url) as ws:
            self._ws = ws
            logger.info("WebSocket connected to %s", self.ws_url)

            # Authenticate via the WebSocket
            await self._authenticate(ws)

            # Reset reconnect counter on successful connection
            self._reconnect_attempt = 0

            async for msg in ws:
                if not self._running:
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WebSocket error: %s", ws.exception())
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    logger.info("WebSocket closed by server")
                    break

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Send authentication challenge over WebSocket."""
        self._seq += 1
        auth_payload = {
            "seq": self._seq,
            "action": "authentication_challenge",
            "data": {"token": self._config.bot_token},
        }
        await ws.send_json(auth_payload)
        logger.info("Sent WebSocket authentication challenge")

    async def _handle_ws_message(self, raw_data: str) -> None:
        """Parse and route a WebSocket message."""
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON WebSocket message: %s", raw_data[:200])
            return

        event = data.get("event")
        seq_reply = data.get("seq_reply")

        if seq_reply:
            status = data.get("status", "")
            logger.info("WebSocket seq_reply=%d status=%s", seq_reply, status)
            return

        if event:
            logger.info("WebSocket event: %s", event)

        # We only care about new_post events
        if event != "posted":
            return

        logger.info("Received 'posted' event")

        post_data = data.get("data", {})
        post_json = post_data.get("post")
        if not post_json:
            logger.warning("Posted event has no post data")
            return

        # The post field is a JSON-encoded string in the WebSocket event
        if isinstance(post_json, str):
            try:
                post = json.loads(post_json)
            except json.JSONDecodeError:
                logger.warning("Failed to parse post JSON")
                return
        else:
            post = post_json

        # Ignore messages not in our target channel
        channel_id = post.get("channel_id", "")
        if channel_id != self._config.channel_id:
            logger.debug("Ignoring post from channel %s (watching %s)", channel_id, self._config.channel_id)
            return

        # Ignore messages from the bot itself
        sender_username = post_data.get("sender_name", "").lstrip("@")
        logger.info("Post in target channel from user: %s", sender_username)
        if sender_username == self._config.bot_username:
            logger.debug("Ignoring own message")
            return

        message_text = post.get("message", "")
        post_id = post.get("id", "")
        user_id = post.get("user_id", "")
        root_id = post.get("root_id", "")

        # Detect music URLs
        music_urls = MUSIC_URL_COMBINED.findall(message_text)

        # Detect @slaptastic commands
        command: str | None = None
        command_args: str | None = None
        cmd_match = COMMAND_PATTERN.search(message_text)
        if cmd_match:
            command = cmd_match.group(1).lower()
            command_args = cmd_match.group(2).strip() if cmd_match.group(2) else None

        incoming = IncomingMessage(
            post_id=post_id,
            channel_id=channel_id,
            user_id=user_id,
            username=sender_username,
            message=message_text,
            root_id=root_id,
            music_urls=music_urls,
            command=command,
            command_args=command_args,
        )

        # Dispatch to registered callbacks
        if music_urls and self._on_music_link:
            try:
                await self._on_music_link(incoming)
            except Exception:
                logger.exception("Error in music link callback")

        if command and self._on_command:
            try:
                await self._on_command(incoming)
            except Exception:
                logger.exception("Error in command callback")
