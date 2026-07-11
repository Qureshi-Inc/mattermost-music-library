"""YouTubeSearcher - searches YouTube for candidates matching a track."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yt_dlp

from app.matching.scorer import CandidateInfo, CandidateScorer, ExpectedMetadata, ScoringWeights

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Result of a YouTube search with scored candidates."""

    query: str
    candidates: list[CandidateInfo]
    best_match: CandidateInfo | None


class YouTubeSearcher:
    """Searches YouTube using yt-dlp and scores results against expected metadata.

    Uses yt-dlp's built-in search feature (ytsearch) to find candidates,
    then scores each one using CandidateScorer.
    """

    def __init__(
        self,
        scorer: CandidateScorer | None = None,
        max_results: int = 10,
        scoring_weights: ScoringWeights | None = None,
    ) -> None:
        """Initialize the searcher.

        Args:
            scorer: CandidateScorer instance (creates default if None).
            max_results: Maximum number of search results to fetch (default 10).
            scoring_weights: Weights for scoring (passed to scorer if scorer is None).
        """
        self.max_results = max_results
        self.scorer = scorer or CandidateScorer(weights=scoring_weights)

    def build_search_query(self, artist: str, title: str) -> str:
        """Construct a YouTube search query from artist and title.

        Combines artist and title in a way that yields good search results.
        """
        # Clean up the artist/title for search
        query = f"{artist} {title}"
        # Remove common noise that hurts search quality
        noise_patterns = [
            " - Single",
            " - EP",
            " (Deluxe)",
            " (Deluxe Edition)",
            " (Remastered)",
            " (Bonus Track)",
        ]
        for noise in noise_patterns:
            query = query.replace(noise, "")
        return query.strip()

    def search(self, artist: str, title: str, expected: ExpectedMetadata | None = None) -> SearchResult:
        """Search YouTube for a track and score the results.

        Args:
            artist: The artist name.
            title: The track title.
            expected: Expected metadata for scoring. If None, one is constructed
                      from artist and title with no duration info.

        Returns:
            SearchResult with scored and sorted candidates.
        """
        query = self.build_search_query(artist, title)
        search_url = f"ytsearch{self.max_results}:{query}"

        if expected is None:
            expected = ExpectedMetadata(title=title, artist=artist)

        logger.info("Searching YouTube: %s", query)

        candidates = self._fetch_candidates(search_url)

        if not candidates:
            logger.warning("No candidates found for query: %s", query)
            return SearchResult(query=query, candidates=[], best_match=None)

        # Score and sort candidates
        scored = self.scorer.score_candidates(candidates, expected)

        best = scored[0] if scored else None
        if best:
            logger.info(
                "Best match: %s (score=%.3f, url=%s)",
                best.title,
                best.score,
                best.url,
            )

        return SearchResult(query=query, candidates=scored, best_match=best)

    def _fetch_candidates(self, search_url: str) -> list[CandidateInfo]:
        """Fetch search results from YouTube using yt-dlp.

        Uses yt-dlp as a Python library (no subprocess).
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "ignoreerrors": True,
            # Don't download anything, just extract info
            "simulate": True,
        }

        candidates: list[CandidateInfo] = []

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(search_url, download=False)

                if result is None:
                    return []

                entries = result.get("entries", [])
                if entries is None:
                    return []

                for entry in entries:
                    if entry is None:
                        continue

                    candidate = self._entry_to_candidate(entry)
                    if candidate is not None:
                        candidates.append(candidate)

        except Exception as exc:
            logger.error("yt-dlp search failed: %s", exc)
            return []

        return candidates

    def _entry_to_candidate(self, entry: dict) -> CandidateInfo | None:
        """Convert a yt-dlp info dict entry to a CandidateInfo."""
        try:
            url = entry.get("webpage_url") or entry.get("url")
            if not url:
                video_id = entry.get("id")
                if video_id:
                    url = f"https://www.youtube.com/watch?v={video_id}"
                else:
                    return None

            title = entry.get("title", "")
            if not title:
                return None

            channel = entry.get("channel") or entry.get("uploader") or ""
            duration = entry.get("duration")  # seconds as float or int
            view_count = entry.get("view_count")

            # Ensure duration is a float if present
            if duration is not None:
                try:
                    duration = float(duration)
                except (TypeError, ValueError):
                    duration = None

            # Ensure view_count is an int if present
            if view_count is not None:
                try:
                    view_count = int(view_count)
                except (TypeError, ValueError):
                    view_count = None

            return CandidateInfo(
                url=url,
                title=title,
                channel=channel,
                duration=duration,
                view_count=view_count,
            )
        except Exception as exc:
            logger.debug("Failed to parse entry: %s", exc)
            return None
