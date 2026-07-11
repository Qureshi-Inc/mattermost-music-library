"""Tests for app.matching.scorer - candidate scoring and matching logic."""

import pytest

from app.matching.scorer import (
    CandidateInfo,
    CandidateScorer,
    ExpectedMetadata,
    ScoringWeights,
)


@pytest.fixture
def scorer():
    """Default scorer with standard weights."""
    return CandidateScorer()


@pytest.fixture
def expected_bohemian():
    """Expected metadata for Bohemian Rhapsody."""
    return ExpectedMetadata(
        title="Bohemian Rhapsody",
        artist="Queen",
        duration_seconds=354.0,
    )


class TestPerfectMatch:
    """Test that perfect matches score near 1.0."""

    def test_perfect_match_scores_high(self, scorer, expected_bohemian):
        """A candidate matching exactly should score near 1.0."""
        candidate = CandidateInfo(
            url="https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=354.0,
            view_count=1_600_000_000,
        )
        score = scorer.score(candidate, expected_bohemian)
        assert score >= 0.85, f"Perfect match scored only {score}"

    def test_exact_title_artist_high_score(self, scorer):
        """Exact title and artist match with correct duration scores very high."""
        expected = ExpectedMetadata(
            title="Blinding Lights",
            artist="The Weeknd",
            duration_seconds=200.0,
        )
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=abc",
            title="The Weeknd - Blinding Lights (Official Audio)",
            channel="TheWeekndVEVO",
            duration=200.0,
            view_count=500_000_000,
        )
        score = scorer.score(candidate, expected)
        assert score >= 0.85


class TestDurationMismatchPenalty:
    """Test that duration mismatches penalize the score."""

    def test_large_duration_mismatch_penalizes(self, scorer, expected_bohemian):
        """A candidate with very different duration gets a lower score."""
        # Good match except duration is way off (live version at 600s vs 354s)
        good_candidate = CandidateInfo(
            url="https://youtube.com/watch?v=good",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=354.0,
            view_count=1_000_000,
        )
        bad_candidate = CandidateInfo(
            url="https://youtube.com/watch?v=bad",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=600.0,  # Way longer - likely a live version
            view_count=1_000_000,
        )
        good_score = scorer.score(good_candidate, expected_bohemian)
        bad_score = scorer.score(bad_candidate, expected_bohemian)
        assert good_score > bad_score, "Duration mismatch should penalize the score"

    def test_small_duration_difference_acceptable(self, scorer, expected_bohemian):
        """A few seconds off in duration should not penalize much."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=close",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=357.0,  # 3 seconds off
            view_count=1_000_000,
        )
        score = scorer.score(candidate, expected_bohemian)
        # Should still score high since 3 seconds off is within tolerance
        assert score >= 0.80

    def test_no_duration_info_neutral(self, scorer, expected_bohemian):
        """Missing duration info should not heavily penalize."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=nodur",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=None,
            view_count=1_000_000,
        )
        score = scorer.score(candidate, expected_bohemian)
        # Should still score reasonably well based on other factors
        assert score >= 0.60


class TestLivePenalty:
    """Test that 'live' in title penalizes when not expected."""

    def test_live_version_penalized_when_not_expected(self, scorer, expected_bohemian):
        """A live version candidate is penalized when studio version is expected."""
        studio_candidate = CandidateInfo(
            url="https://youtube.com/watch?v=studio",
            title="Queen - Bohemian Rhapsody (Official Video)",
            channel="Queen Official",
            duration=354.0,
            view_count=1_000_000,
        )
        live_candidate = CandidateInfo(
            url="https://youtube.com/watch?v=live",
            title="Queen - Bohemian Rhapsody (Live at Wembley 1986)",
            channel="Queen Official",
            duration=354.0,
            view_count=500_000,
        )
        studio_score = scorer.score(studio_candidate, expected_bohemian)
        live_score = scorer.score(live_candidate, expected_bohemian)
        assert studio_score > live_score, "Live version should score lower when not expected"

    def test_live_not_penalized_when_expected(self, scorer):
        """A live version is NOT penalized when explicitly expected."""
        expected = ExpectedMetadata(
            title="Bohemian Rhapsody",
            artist="Queen",
            duration_seconds=400.0,
            is_live=True,
        )
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=live",
            title="Queen - Bohemian Rhapsody (Live at Wembley 1986)",
            channel="Queen Official",
            duration=400.0,
            view_count=500_000,
        )
        score = scorer.score(candidate, expected)
        # Should not be penalized for live content
        assert score >= 0.70


class TestOfficialBonus:
    """Test that 'official' in title provides a bonus."""

    def test_official_audio_gets_bonus(self, scorer, expected_bohemian):
        """Candidates with 'official audio' score higher than those without."""
        official = CandidateInfo(
            url="https://youtube.com/watch?v=off",
            title="Queen - Bohemian Rhapsody (Official Audio)",
            channel="SomeChannel",
            duration=354.0,
            view_count=100_000,
        )
        unofficial = CandidateInfo(
            url="https://youtube.com/watch?v=unoff",
            title="Queen - Bohemian Rhapsody",
            channel="SomeChannel",
            duration=354.0,
            view_count=100_000,
        )
        official_score = scorer.score(official, expected_bohemian)
        unofficial_score = scorer.score(unofficial, expected_bohemian)
        assert official_score > unofficial_score, "'Official' should boost score"

    def test_official_music_video_gets_bonus(self, scorer, expected_bohemian):
        """Official music video gets the bonus."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=omv",
            title="Queen - Bohemian Rhapsody (Official Music Video)",
            channel="Queen Official",
            duration=354.0,
            view_count=1_000_000,
        )
        score = scorer.score(candidate, expected_bohemian)
        assert score >= 0.85


class TestVEVOChannelBonus:
    """Test that VEVO channels receive a reputation bonus."""

    def test_vevo_channel_scores_higher(self, scorer, expected_bohemian):
        """A VEVO channel should score higher than a random channel."""
        vevo = CandidateInfo(
            url="https://youtube.com/watch?v=vevo",
            title="Queen - Bohemian Rhapsody",
            channel="QueenVEVO",
            duration=354.0,
            view_count=100_000,
        )
        random_channel = CandidateInfo(
            url="https://youtube.com/watch?v=random",
            title="Queen - Bohemian Rhapsody",
            channel="xXMusicFan123Xx",
            duration=354.0,
            view_count=100_000,
        )
        vevo_score = scorer.score(vevo, expected_bohemian)
        random_score = scorer.score(random_channel, expected_bohemian)
        assert vevo_score > random_score, "VEVO channel should score higher"


class TestRemixPenalty:
    """Test that remix versions are penalized when not expected."""

    def test_remix_penalized_when_not_expected(self, scorer):
        """A remix candidate is penalized when original is expected."""
        expected = ExpectedMetadata(
            title="Blinding Lights",
            artist="The Weeknd",
            duration_seconds=200.0,
        )
        original = CandidateInfo(
            url="https://youtube.com/watch?v=orig",
            title="The Weeknd - Blinding Lights (Official Audio)",
            channel="TheWeekndVEVO",
            duration=200.0,
            view_count=100_000,
        )
        remix = CandidateInfo(
            url="https://youtube.com/watch?v=remix",
            title="The Weeknd - Blinding Lights (Remix) ft. Someone",
            channel="TheWeekndVEVO",
            duration=200.0,
            view_count=100_000,
        )
        original_score = scorer.score(original, expected)
        remix_score = scorer.score(remix, expected)
        assert original_score > remix_score, "Remix should score lower when not expected"


class TestScoreCandidates:
    """Test the batch scoring and sorting functionality."""

    def test_candidates_sorted_by_score_descending(self, scorer, expected_bohemian):
        """score_candidates returns candidates sorted best-first."""
        c1 = CandidateInfo(
            url="https://youtube.com/watch?v=1",
            title="Random Song",
            channel="Random",
            duration=180.0,
            view_count=100,
        )
        c2 = CandidateInfo(
            url="https://youtube.com/watch?v=2",
            title="Queen - Bohemian Rhapsody (Official Audio)",
            channel="QueenVEVO",
            duration=354.0,
            view_count=1_000_000_000,
        )
        c3 = CandidateInfo(
            url="https://youtube.com/watch?v=3",
            title="Queen - Bohemian Rhapsody (Live Concert)",
            channel="ConcertFan",
            duration=600.0,
            view_count=50_000,
        )
        results = scorer.score_candidates([c1, c2, c3], expected_bohemian)
        assert results[0].url == c2.url, "Best match should be first"
        assert results[0].score >= results[1].score >= results[2].score

    def test_score_candidates_mutates_score_field(self, scorer, expected_bohemian):
        """score_candidates sets the .score field on each candidate."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=1",
            title="Queen - Bohemian Rhapsody",
            channel="Queen",
            duration=354.0,
            view_count=1000,
        )
        assert candidate.score == 0.0
        scorer.score_candidates([candidate], expected_bohemian)
        assert candidate.score > 0.0


class TestCustomWeights:
    """Test that custom scoring weights change behavior."""

    def test_title_only_weights(self, expected_bohemian):
        """With title_similarity as only weight, title match dominates."""
        weights = ScoringWeights(
            title_similarity=1.0,
            duration_match=0.0,
            channel_reputation=0.0,
            view_count=0.0,
            official_in_title=0.0,
            unwanted_content_penalty=0.0,
        )
        scorer = CandidateScorer(weights=weights)

        good_title = CandidateInfo(
            url="https://youtube.com/watch?v=1",
            title="Queen - Bohemian Rhapsody",
            channel="Random",
            duration=999.0,  # Wrong duration but weight is 0
            view_count=1,
        )
        bad_title = CandidateInfo(
            url="https://youtube.com/watch?v=2",
            title="Completely Unrelated Video",
            channel="QueenVEVO",  # Good channel but weight is 0
            duration=354.0,  # Perfect duration but weight is 0
            view_count=1_000_000_000,
        )
        good_score = scorer.score(good_title, expected_bohemian)
        bad_score = scorer.score(bad_title, expected_bohemian)
        assert good_score > bad_score


class TestEdgeCases:
    """Test edge cases in scoring."""

    def test_score_always_between_0_and_1(self, scorer):
        """Score is always between 0.0 and 1.0 regardless of inputs."""
        expected = ExpectedMetadata(title="X", artist="Y", duration_seconds=100.0)
        candidates = [
            CandidateInfo(url="u", title="", channel="", duration=None, view_count=None),
            CandidateInfo(url="u", title="X" * 1000, channel="Z" * 1000, duration=99999.0, view_count=0),
            CandidateInfo(url="u", title="Y - X", channel="YVEVO", duration=100.0, view_count=10**10),
        ]
        for c in candidates:
            score = scorer.score(c, expected)
            assert 0.0 <= score <= 1.0, f"Score {score} is out of range for {c.title}"

    def test_zero_view_count_handled(self, scorer, expected_bohemian):
        """Zero view count does not crash."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=zero",
            title="Queen - Bohemian Rhapsody",
            channel="Queen",
            duration=354.0,
            view_count=0,
        )
        score = scorer.score(candidate, expected_bohemian)
        assert 0.0 <= score <= 1.0

    def test_negative_view_count_handled(self, scorer, expected_bohemian):
        """Negative view count (corrupted data) does not crash."""
        candidate = CandidateInfo(
            url="https://youtube.com/watch?v=neg",
            title="Queen - Bohemian Rhapsody",
            channel="Queen",
            duration=354.0,
            view_count=-1,
        )
        score = scorer.score(candidate, expected_bohemian)
        assert 0.0 <= score <= 1.0
