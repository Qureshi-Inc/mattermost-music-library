"""Library file organizer - moves and renames music files into a structured hierarchy."""

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Characters that are invalid in filenames across common filesystems
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Maximum filename length (excluding extension) - conservative for cross-platform
MAX_FILENAME_LENGTH = 200

# Maximum directory name length
MAX_DIR_LENGTH = 100


class LibraryOrganizer:
    """Organizes music files into a structured directory hierarchy.

    Target structure: {base_path}/Artist/Album/01 - Song Title.mp3

    Handles:
        - Filename sanitization (invalid chars, length limits)
        - Directory creation
        - File conflicts (appends incrementing number)
        - Track number zero-padding to 2 digits
    """

    def __init__(self, base_path: Path) -> None:
        """Initialize the organizer with a base music directory.

        Args:
            base_path: Root directory for the music library (e.g., /music).
        """
        self.base_path = Path(base_path)

    def organize(
        self,
        source_path: Path,
        artist: str,
        album: str,
        title: str,
        track_number: int | None = None,
        move: bool = True,
        artwork_url: str | None = None,
    ) -> Path:
        """Organize a music file into the library hierarchy.

        Moves (or copies) the file to: {base_path}/{artist}/{album}/{track} - {title}.mp3

        Args:
            source_path: Current path to the music file.
            artist: Artist name for directory and filename.
            album: Album name for directory.
            title: Track title for filename.
            track_number: Optional track number (zero-padded to 2 digits).
            move: If True, moves the file. If False, copies it.

        Returns:
            The final destination Path where the file now resides.

        Raises:
            FileNotFoundError: If source_path does not exist.
            ValueError: If artist or title is empty after sanitization.
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        # Sanitize components
        safe_artist = self._sanitize_dirname(artist)
        safe_album = self._sanitize_dirname(album) if album else "Unknown Album"
        safe_title = self._sanitize_filename(title)

        if not safe_artist:
            raise ValueError(f"Artist name is empty after sanitization: '{artist}'")
        if not safe_title:
            raise ValueError(f"Title is empty after sanitization: '{title}'")

        # Build filename: "01 - Song Title.mp3"
        extension = source.suffix.lower() or ".mp3"
        if track_number is not None:
            filename = f"{track_number:02d} - {safe_title}{extension}"
        else:
            filename = f"{safe_title}{extension}"

        # Build destination path
        dest_dir = self.base_path / safe_artist / safe_album
        dest_path = dest_dir / filename

        # Create directory structure
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Handle conflicts by appending a number
        dest_path = self._resolve_conflict(dest_path)

        # Move or copy the file
        if move:
            shutil.move(str(source), str(dest_path))
            logger.info("Moved file to library", extra={"dest": str(dest_path)})
        else:
            shutil.copy2(str(source), str(dest_path))
            logger.info("Copied file to library", extra={"dest": str(dest_path)})

        # Save cover.jpg for album-level artwork in Jellyfin
        if artwork_url:
            self._save_cover_art(dest_dir, artwork_url)

        return dest_path

    def _save_cover_art(self, album_dir: Path, artwork_url: str) -> None:
        """Download and save cover.jpg in the album folder if not already present."""
        cover_path = album_dir / "cover.jpg"
        if cover_path.exists():
            return

        try:
            import httpx
            # Upscale iTunes artwork
            if "mzstatic.com" in artwork_url:
                artwork_url = artwork_url.replace("100x100", "600x600").replace("60x60", "600x600")

            with httpx.Client(timeout=10, follow_redirects=True) as client:
                resp = client.get(artwork_url)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    cover_path.write_bytes(resp.content)
                    logger.info("Saved cover.jpg", extra={"path": str(cover_path)})
                elif "img.youtube.com" in artwork_url and "maxresdefault" in artwork_url:
                    # Fallback to hqdefault
                    fallback = artwork_url.replace("maxresdefault", "hqdefault")
                    resp = client.get(fallback)
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        cover_path.write_bytes(resp.content)
                        logger.info("Saved cover.jpg (hqdefault)", extra={"path": str(cover_path)})
        except Exception as e:
            logger.warning("Failed to save cover art: %s", e)

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use as a filename component.

        Removes invalid characters, collapses whitespace, trims to max length.

        Args:
            name: Raw filename string.

        Returns:
            Sanitized string safe for use in filenames.
        """
        # Remove invalid characters
        sanitized = INVALID_FILENAME_CHARS.sub("", name)

        # Replace runs of whitespace with a single space
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        # Remove leading/trailing dots and spaces (problematic on Windows/macOS)
        sanitized = sanitized.strip(". ")

        # Truncate to max length
        if len(sanitized) > MAX_FILENAME_LENGTH:
            sanitized = sanitized[:MAX_FILENAME_LENGTH].rstrip()

        return sanitized

    def _sanitize_dirname(self, name: str) -> str:
        """Sanitize a string for use as a directory name.

        Similar to filename sanitization but with a shorter max length.

        Args:
            name: Raw directory name string.

        Returns:
            Sanitized string safe for use as a directory name.
        """
        # Remove invalid characters
        sanitized = INVALID_FILENAME_CHARS.sub("", name)

        # Replace runs of whitespace with a single space
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        # Remove leading/trailing dots and spaces
        sanitized = sanitized.strip(". ")

        # Truncate to max length
        if len(sanitized) > MAX_DIR_LENGTH:
            sanitized = sanitized[:MAX_DIR_LENGTH].rstrip()

        return sanitized if sanitized else "Unknown"

    def _resolve_conflict(self, dest_path: Path) -> Path:
        """Resolve filename conflicts by appending an incrementing number.

        If dest_path exists, tries "name (1).ext", "name (2).ext", etc.

        Args:
            dest_path: The desired destination path.

        Returns:
            A path that does not currently exist.
        """
        if not dest_path.exists():
            return dest_path

        stem = dest_path.stem
        suffix = dest_path.suffix
        parent = dest_path.parent

        counter = 1
        while True:
            new_name = f"{stem} ({counter}){suffix}"
            candidate = parent / new_name
            if not candidate.exists():
                logger.warning(
                    "File conflict resolved",
                    extra={
                        "original": str(dest_path),
                        "resolved": str(candidate),
                    },
                )
                return candidate
            counter += 1

            # Safety valve - shouldn't happen in practice
            if counter > 9999:
                raise RuntimeError(
                    f"Cannot resolve conflict after 9999 attempts: {dest_path}"
                )
