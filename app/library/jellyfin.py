"""Jellyfin API client for triggering library refreshes."""

import logging

import aiohttp

logger = logging.getLogger(__name__)


class JellyfinClient:
    """Async client for the Jellyfin media server API.

    Supports triggering full library refreshes and targeted music library scans.
    Authenticates via X-Emby-Token header with an API key.
    """

    def __init__(self, base_url: str, api_token: str) -> None:
        """Initialize the Jellyfin client.

        Args:
            base_url: Jellyfin server base URL (e.g., http://localhost:8096).
            api_token: Jellyfin API key for authentication.
        """
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._session: aiohttp.ClientSession | None = None

    @property
    def _headers(self) -> dict[str, str]:
        """Authentication headers for Jellyfin API requests."""
        return {
            "X-Emby-Token": self.api_token,
            "Content-Type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def refresh_library(self) -> bool:
        """Trigger a full library refresh on Jellyfin.

        Sends POST /Library/Refresh to scan all libraries for new content.

        Returns:
            True if the refresh was triggered successfully, False otherwise.
        """
        url = f"{self.base_url}/Library/Refresh"
        try:
            session = await self._get_session()
            async with session.post(url, headers=self._headers) as resp:
                if resp.status == 204:
                    logger.info("Jellyfin library refresh triggered successfully")
                    return True
                else:
                    body = await resp.text()
                    logger.error(
                        "Jellyfin library refresh failed",
                        extra={"status": resp.status, "body": body},
                    )
                    return False
        except aiohttp.ClientConnectorError as e:
            logger.error(
                "Cannot connect to Jellyfin",
                extra={"url": self.base_url, "error": str(e)},
            )
            return False
        except aiohttp.ClientError as e:
            logger.error(
                "Jellyfin API error during refresh",
                extra={"error": str(e)},
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error during Jellyfin refresh",
                extra={"error": str(e)},
            )
            return False

    async def refresh_music_library(self) -> bool:
        """Trigger a scan of only the music library on Jellyfin.

        Finds the music library by type and triggers a targeted refresh.

        Returns:
            True if the music library scan was triggered, False otherwise.
        """
        library_id = await self._get_music_library_id()
        if not library_id:
            logger.warning(
                "No music library found on Jellyfin, falling back to full refresh"
            )
            return await self.refresh_library()

        url = f"{self.base_url}/Items/{library_id}/Refresh"
        try:
            session = await self._get_session()
            params = {
                "Recursive": "true",
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false",
                "ReplaceAllImages": "false",
            }
            async with session.post(
                url, headers=self._headers, params=params
            ) as resp:
                if resp.status in (200, 204):
                    logger.info(
                        "Jellyfin music library scan triggered",
                        extra={"library_id": library_id},
                    )
                    return True
                else:
                    body = await resp.text()
                    logger.error(
                        "Jellyfin music library scan failed",
                        extra={
                            "status": resp.status,
                            "body": body,
                            "library_id": library_id,
                        },
                    )
                    return False
        except aiohttp.ClientConnectorError as e:
            logger.error(
                "Cannot connect to Jellyfin for music scan",
                extra={"url": self.base_url, "error": str(e)},
            )
            return False
        except aiohttp.ClientError as e:
            logger.error(
                "Jellyfin API error during music scan",
                extra={"error": str(e)},
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected error during Jellyfin music scan",
                extra={"error": str(e)},
            )
            return False

    async def _get_music_library_id(self) -> str | None:
        """Find the ID of the music library in Jellyfin.

        Queries the virtual folders endpoint and finds the library
        with CollectionType == "music".

        Returns:
            The library item ID if found, None otherwise.
        """
        url = f"{self.base_url}/Library/VirtualFolders"
        try:
            session = await self._get_session()
            async with session.get(url, headers=self._headers) as resp:
                if resp.status != 200:
                    logger.error(
                        "Failed to list Jellyfin libraries",
                        extra={"status": resp.status},
                    )
                    return None

                folders = await resp.json()
                for folder in folders:
                    if folder.get("CollectionType", "").lower() == "music":
                        return folder.get("ItemId")  # type: ignore[no-any-return]

                return None
        except (aiohttp.ClientError, Exception) as e:
            logger.error(
                "Error fetching Jellyfin library list",
                extra={"error": str(e)},
            )
            return None

    async def search_track(self, title: str, artist: str) -> str | None:
        """Search for a track in Jellyfin by title and artist.

        Tries multiple search strategies: full query, title only, simplified title.
        Returns the item ID if found, None otherwise.
        """
        import re

        # Clean the title for searching
        clean_title = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()  # Remove parentheticals
        clean_title = re.sub(r'\s*-\s*$', '', clean_title).strip()

        # Try multiple search queries in order of specificity
        queries = []
        if clean_title and artist:
            queries.append(f"{clean_title} {artist}")
        if clean_title:
            queries.append(clean_title)
        if title != clean_title:
            queries.append(title)

        session = await self._get_session()
        for query in queries:
            try:
                params = {
                    "searchTerm": query,
                    "IncludeItemTypes": "Audio",
                    "Recursive": "true",
                    "Limit": "5",
                }
                async with session.get(
                    f"{self.base_url}/Items", headers=self._headers, params=params
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                items = data.get("Items", [])
                if not items:
                    continue

                # Find best match
                norm_title = clean_title.lower() if clean_title else title.lower()
                for item in items:
                    item_name = (item.get("Name") or "").lower()
                    if norm_title in item_name or item_name in norm_title:
                        return item.get("Id")

                # Accept first result if query was specific enough
                if len(query.split()) >= 2:
                    return items[0].get("Id")
            except Exception as e:
                logger.warning("Jellyfin search failed for query '%s': %s", query, e)

        return None

    async def get_or_create_playlist(self, playlist_name: str, user_id: str) -> str | None:
        """Get an existing playlist by name or create a new one.

        Args:
            playlist_name: Name of the playlist.
            user_id: Jellyfin user ID who owns the playlist.

        Returns:
            The playlist ID, or None on failure.
        """
        session = await self._get_session()

        # Search for existing playlist
        try:
            params = {
                "IncludeItemTypes": "Playlist",
                "Recursive": "true",
                "searchTerm": playlist_name,
            }
            async with session.get(
                f"{self.base_url}/Items", headers=self._headers, params=params
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("Items", []):
                        if item.get("Name", "").lower() == playlist_name.lower():
                            return item.get("Id")
        except Exception as e:
            logger.warning("Playlist search failed: %s", e)

        # Create new playlist
        try:
            async with session.post(
                f"{self.base_url}/Playlists",
                headers=self._headers,
                json={"Name": playlist_name, "UserId": user_id, "MediaType": "Audio", "Ids": []},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    playlist_id = data.get("Id")
                    logger.info("Created Jellyfin playlist: %s (id=%s)", playlist_name, playlist_id)
                    return playlist_id
                else:
                    body = await resp.text()
                    logger.error("Failed to create playlist: %d %s", resp.status, body)
                    return None
        except Exception as e:
            logger.error("Playlist creation error: %s", e)
            return None

    async def add_to_playlist(self, playlist_id: str, item_id: str, user_id: str) -> bool:
        """Add a track to a playlist.

        Args:
            playlist_id: The playlist to add to.
            item_id: The track item ID.
            user_id: The user who owns the playlist.

        Returns:
            True if successful.
        """
        session = await self._get_session()
        try:
            url = f"{self.base_url}/Playlists/{playlist_id}/Items?Ids={item_id}&UserId={user_id}"
            async with session.post(url, headers=self._headers) as resp:
                if resp.status == 204:
                    return True
                else:
                    logger.warning("Failed to add item to playlist: %d", resp.status)
                    return False
        except Exception as e:
            logger.error("Add to playlist error: %s", e)
            return False

    async def get_admin_user_id(self) -> str | None:
        """Get the first admin user's ID (used as playlist owner)."""
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}/Users", headers=self._headers) as resp:
                if resp.status != 200:
                    return None
                users = await resp.json()
                for user in users:
                    if user.get("Policy", {}).get("IsAdministrator"):
                        return user.get("Id")
                # Fall back to first user
                return users[0].get("Id") if users else None
        except Exception:
            return None

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
