"""Matching module - scores YouTube candidates and detects duplicates."""

from app.matching.dedup import DuplicateDetector
from app.matching.scorer import CandidateScorer
from app.matching.searcher import YouTubeSearcher

__all__ = ["CandidateScorer", "YouTubeSearcher", "DuplicateDetector"]
