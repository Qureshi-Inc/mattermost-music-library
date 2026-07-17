"""CandidateScorer - scores YouTube search results against expected metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class ScoringWeights:
    """Configurable weights for each scoring factor.

    All weights should sum to roughly 1.0 for a normalized final score,
    but the scorer normalizes them internally so any positive values work.
    """

    title_similarity: float = 0.32
    duration_match: float = 0.34
    channel_reputation: float = 0.12
    view_count: float = 0.05
    official_in_title: float = 0.08
    unwanted_content_penalty: float = 0.09


@dataclass
class CandidateInfo:
    """Metadata about a YouTube search result candidate."""

    url: str
    title: str
    channel: str
    duration: float | None  # seconds
    view_count: int | None

    # Computed after scoring
    score: float = 0.0


@dataclass
class ExpectedMetadata:
    """The metadata we expect for the track we're trying to match."""

    title: str
    artist: str
    duration_seconds: float | None = None
    is_live: bool = False
    is_remix: bool = False
    is_cover: bool = False


class CandidateScorer:
    """Scores YouTube candidates against expected track metadata.

    Each candidate receives a float score between 0.0 and 1.0 indicating
    how well it matches the expected track.
    """

    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self.weights = weights or ScoringWeights()
        self._official_patterns = [
            re.compile(r"vevo$", re.IGNORECASE),
            re.compile(r"official", re.IGNORECASE),
            re.compile(r"records$", re.IGNORECASE),
            re.compile(r"music$", re.IGNORECASE),
        ]

    def score(self, candidate: CandidateInfo, expected: ExpectedMetadata) -> float:
        """Score a single candidate against expected metadata.

        Returns a float between 0.0 and 1.0.
        """
        w = self.weights
        total_weight = (
            w.title_similarity
            + w.duration_match
            + w.channel_reputation
            + w.view_count
            + w.official_in_title
            + w.unwanted_content_penalty
        )

        if total_weight == 0:
            return 0.0

        weighted_score = 0.0

        # Factor 1: Title similarity (fuzzy match)
        title_score = self._score_title_similarity(candidate.title, expected)
        weighted_score += title_score * w.title_similarity

        # Factor 2: Duration match
        duration_score = self._score_duration_match(candidate.duration, expected.duration_seconds)
        weighted_score += duration_score * w.duration_match

        # Factor 3: Channel reputation
        channel_score = self._score_channel_reputation(candidate.channel)
        weighted_score += channel_score * w.channel_reputation

        # Factor 4: View count
        view_score = self._score_view_count(candidate.view_count)
        weighted_score += view_score * w.view_count

        # Factor 5: "official" in title bonus
        official_score = self._score_official_in_title(candidate.title)
        weighted_score += official_score * w.official_in_title

        # Factor 6: Unwanted content penalty (live/remix/cover when not expected)
        unwanted_score = self._score_unwanted_content(candidate.title, expected)
        weighted_score += unwanted_score * w.unwanted_content_penalty

        # Normalize to 0.0-1.0
        final_score = weighted_score / total_weight
        return max(0.0, min(1.0, final_score))

    def score_candidates(
        self, candidates: list[CandidateInfo], expected: ExpectedMetadata
    ) -> list[CandidateInfo]:
        """Score all candidates and return them sorted by score descending.

        Mutates each candidate's .score field in place and returns the sorted list.
        """
        for candidate in candidates:
            candidate.score = self.score(candidate, expected)
        return sorted(candidates, key=lambda c: c.score, reverse=True)

    def _score_title_similarity(self, candidate_title: str, expected: ExpectedMetadata) -> float:
        """Score how similar the candidate title is to expected artist + title.

        Uses SequenceMatcher for fuzzy string matching.
        """
        # Build expected string variants to compare against
        expected_full = f"{expected.artist} - {expected.title}".lower()
        expected_title_only = expected.title.lower()
        candidate_lower = candidate_title.lower()

        # Try full "artist - title" match
        full_ratio = SequenceMatcher(None, candidate_lower, expected_full).ratio()

        # Try just title match (artist might be in channel name)
        title_ratio = SequenceMatcher(None, candidate_lower, expected_title_only).ratio()

        # Also check if both artist and title are substrings
        artist_lower = expected.artist.lower()
        contains_artist = artist_lower in candidate_lower
        contains_title = expected_title_only in candidate_lower
        substring_score = 0.0
        if contains_artist and contains_title:
            substring_score = 0.95
        elif contains_title:
            substring_score = 0.75
        elif contains_artist:
            substring_score = 0.4

        # Take the best score
        return max(full_ratio, title_ratio, substring_score)

    def _score_duration_match(
        self, candidate_duration: float | None, expected_duration: float | None
    ) -> float:
        """Score how close the duration is to expected.

        Within 5s = high score (1.0)
        Within 10s = medium score (0.7)
        Within 15s = low score (0.4)
        Beyond 15s = penalty (0.1)
        No duration info = neutral (0.5)
        """
        if candidate_duration is None or expected_duration is None:
            return 0.5  # Neutral when we can't compare

        diff = abs(candidate_duration - expected_duration)

        if diff <= 5:
            return 1.0
        elif diff <= 10:
            # Linear interpolation from 1.0 at 5s to 0.7 at 10s
            return 1.0 - (diff - 5) * 0.06
        elif diff <= 15:
            # Linear interpolation from 0.7 at 10s to 0.4 at 15s
            return 0.7 - (diff - 10) * 0.06
        else:
            # Heavy penalty for large duration mismatch
            return 0.1

    def _score_channel_reputation(self, channel: str) -> float:
        """Score channel reputation based on known patterns.

        Official/VEVO channels get a bonus.
        """
        if not channel:
            return 0.5

        for pattern in self._official_patterns:
            if pattern.search(channel):
                return 1.0

        # Check if channel looks like an artist name (no special chars, reasonable length)
        if len(channel) < 50 and not re.search(r"[^\w\s\-.]", channel):
            return 0.6

        return 0.4

    def _score_view_count(self, view_count: int | None) -> float:
        """Score based on view count - higher is slightly better.

        Uses logarithmic scaling to prevent extreme values from dominating.
        """
        if view_count is None or view_count <= 0:
            return 0.3

        # Logarithmic scale: 1K=0.4, 10K=0.5, 100K=0.6, 1M=0.7, 10M=0.8, 100M=0.9, 1B=1.0
        import math

        log_views = math.log10(max(view_count, 1))
        # Map log10(views) from 3 (1000) to 9 (1B) onto 0.4 to 1.0
        score = 0.4 + (log_views - 3) * 0.1
        return max(0.2, min(1.0, score))

    def _score_official_in_title(self, title: str) -> float:
        """Bonus for having 'official' in the title."""
        title_lower = title.lower()

        official_indicators = [
            "official audio",
            "official music video",
            "official video",
            "official lyric",
        ]

        for indicator in official_indicators:
            if indicator in title_lower:
                return 1.0

        if "official" in title_lower:
            return 0.8

        return 0.3

    def _score_unwanted_content(self, title: str, expected: ExpectedMetadata) -> float:
        """Penalize live/remix/cover content when not expected.

        Returns 1.0 (good) when no unwanted content is detected.
        Returns lower scores when unwanted markers are found.
        """
        title_lower = title.lower()
        penalty = 0.0

        # Check for "live" when not expected
        live_patterns = [" live ", "(live)", "[live]", "live at ", "live from ", "concert"]
        if not expected.is_live:
            for pattern in live_patterns:
                if pattern in title_lower:
                    penalty += 0.4
                    break

        # Check for "remix" when not expected
        remix_patterns = ["remix", "remixed"]
        if not expected.is_remix:
            for pattern in remix_patterns:
                if pattern in title_lower:
                    penalty += 0.4
                    break

        # Check for "cover" when not expected
        cover_patterns = ["cover", "covered by", "tribute"]
        if not expected.is_cover:
            for pattern in cover_patterns:
                if pattern in title_lower:
                    penalty += 0.4
                    break

        # Additional penalties for other undesirable content
        undesirable = [
            "karaoke", "instrumental", "8d audio", "slowed", "sped up", "nightcore",
            "reaction", "reacts", "review", "reviewed", "explained", "meaning",
            "lesson", "tutorial", "how to play", "guitar chords", "piano tutorial",
            "mashup", "loop", "1 hour", "1hour", "extended loop",
        ]
        for pattern in undesirable:
            if pattern in title_lower:
                penalty += 0.35
                break

        return max(0.0, 1.0 - penalty)
