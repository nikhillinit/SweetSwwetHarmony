"""
Tests for Traction Calculator - calculates momentum metrics (Harmonic-inspired).

TDD Approach: Tests written before implementation.

Traction metrics include:
- GitHub momentum (stars growth, commit velocity)
- Hiring velocity (job posting frequency)
- Social momentum (Product Hunt votes, HN mentions)
- Composite momentum score with percentile ranking
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from typing import List, Dict, Any


class TestCalculateGitHubMomentum:
    """Test GitHub momentum calculation (stars growth, commit velocity)."""

    @pytest.mark.asyncio
    async def test_calculate_stars_growth_positive(self):
        """Should calculate positive stars growth percentage."""
        from utils.traction_calculator import TractionCalculator, TractionScore

        mock_store = AsyncMock()

        # Two snapshots: 100 stars 30 days ago, 150 stars now = 50% growth
        signals = [
            {
                "id": 1,
                "signal_type": "github_trending",
                "detected_at": datetime.now(timezone.utc) - timedelta(days=30),
                "raw_data": {"stars": 100, "forks": 10},
            },
            {
                "id": 2,
                "signal_type": "github_trending",
                "detected_at": datetime.now(timezone.utc),
                "raw_data": {"stars": 150, "forks": 15},
            },
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_github_momentum("domain:test.com")

        assert result["stars_growth_30d"] == pytest.approx(0.5, rel=0.01)  # 50% growth
        assert result["has_data"] is True

    @pytest.mark.asyncio
    async def test_calculate_stars_growth_negative(self):
        """Should handle negative/zero growth gracefully."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()

        # Stars decreased: 200 -> 180 = -10% growth
        signals = [
            {
                "id": 1,
                "signal_type": "github_trending",
                "detected_at": datetime.now(timezone.utc) - timedelta(days=30),
                "raw_data": {"stars": 200},
            },
            {
                "id": 2,
                "signal_type": "github_trending",
                "detected_at": datetime.now(timezone.utc),
                "raw_data": {"stars": 180},
            },
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_github_momentum("domain:test.com")

        assert result["stars_growth_30d"] == pytest.approx(-0.1, rel=0.01)

    @pytest.mark.asyncio
    async def test_github_momentum_no_data(self):
        """Should return zeros when no GitHub signals exist."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()
        mock_store.get_signals_for_traction = AsyncMock(return_value=[])

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_github_momentum("domain:unknown.com")

        assert result["stars_growth_30d"] == 0.0
        assert result["has_data"] is False


class TestCalculateHiringVelocity:
    """Test hiring velocity calculation (job posting frequency)."""

    @pytest.mark.asyncio
    async def test_calculate_job_posting_velocity(self):
        """Should calculate job postings per week."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()

        # 8 job postings over 4 weeks = 2 per week
        now = datetime.now(timezone.utc)
        signals = [
            {"id": i, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=i * 3)}
            for i in range(8)
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_hiring_velocity("domain:startup.io")

        # 8 postings in ~24 days = ~2.3 per week
        assert result["job_posting_velocity"] >= 2.0
        assert result["job_count_30d"] == 8

    @pytest.mark.asyncio
    async def test_hiring_velocity_growth(self):
        """Should calculate hiring growth between periods."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()

        now = datetime.now(timezone.utc)
        # 2 postings in days 60-30, 6 postings in days 30-0 = 200% growth
        signals = [
            # Old period (60-30 days ago)
            {"id": 1, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=45)},
            {"id": 2, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=50)},
            # Recent period (last 30 days)
            {"id": 3, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=5)},
            {"id": 4, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=10)},
            {"id": 5, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=15)},
            {"id": 6, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=20)},
            {"id": 7, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=25)},
            {"id": 8, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=28)},
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_hiring_velocity("domain:growing.ai")

        # 6 recent vs 2 old = 200% growth
        assert result["job_count_growth_30d"] >= 1.5  # At least 150% growth

    @pytest.mark.asyncio
    async def test_hiring_velocity_no_postings(self):
        """Should return zeros when no job postings exist."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()
        mock_store.get_signals_for_traction = AsyncMock(return_value=[])

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_hiring_velocity("domain:quiet.com")

        assert result["job_posting_velocity"] == 0.0
        assert result["job_count_30d"] == 0


class TestCalculateSocialMomentum:
    """Test social momentum (Product Hunt, Hacker News)."""

    @pytest.mark.asyncio
    async def test_product_hunt_vote_growth(self):
        """Should calculate Product Hunt upvote growth."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()

        signals = [
            {
                "id": 1,
                "signal_type": "product_hunt_launch",
                "detected_at": datetime.now(timezone.utc) - timedelta(days=30),
                "raw_data": {"upvotes": 100, "comments": 20},
            },
            {
                "id": 2,
                "signal_type": "product_hunt_launch",
                "detected_at": datetime.now(timezone.utc),
                "raw_data": {"upvotes": 250, "comments": 50},
            },
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_social_momentum("domain:viral.io")

        # 100 -> 250 = 150% growth
        assert result["ph_vote_growth_30d"] >= 1.0  # At least 100% growth
        assert result["has_social_data"] is True

    @pytest.mark.asyncio
    async def test_hacker_news_mentions(self):
        """Should count Hacker News mention growth."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()

        now = datetime.now(timezone.utc)
        signals = [
            # 2 mentions in old period
            {"id": 1, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=45)},
            {"id": 2, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=50)},
            # 5 mentions in recent period
            {"id": 3, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=5)},
            {"id": 4, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=10)},
            {"id": 5, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=15)},
            {"id": 6, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=20)},
            {"id": 7, "signal_type": "hacker_news_mention", "detected_at": now - timedelta(days=25)},
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate_social_momentum("domain:hn-famous.com")

        # 5 recent vs 2 old = 150% growth
        assert result["hn_mention_growth_30d"] >= 1.0
        assert result["hn_mention_count_30d"] == 5


class TestCompositeMomentum:
    """Test composite momentum score calculation."""

    @pytest.mark.asyncio
    async def test_composite_momentum_weighted_average(self):
        """Should calculate weighted composite from all momentum sources."""
        from utils.traction_calculator import TractionCalculator, TractionScore

        mock_store = AsyncMock()

        # Mixed signals
        now = datetime.now(timezone.utc)
        signals = [
            # GitHub: 100 -> 200 stars (100% growth)
            {"id": 1, "signal_type": "github_trending", "detected_at": now - timedelta(days=30), "raw_data": {"stars": 100}},
            {"id": 2, "signal_type": "github_trending", "detected_at": now, "raw_data": {"stars": 200}},
            # Hiring: 4 recent postings
            {"id": 3, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=5)},
            {"id": 4, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=10)},
            {"id": 5, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=15)},
            {"id": 6, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=20)},
            # Product Hunt: 50 -> 100 upvotes (100% growth)
            {"id": 7, "signal_type": "product_hunt_launch", "detected_at": now - timedelta(days=30), "raw_data": {"upvotes": 50}},
            {"id": 8, "signal_type": "product_hunt_launch", "detected_at": now, "raw_data": {"upvotes": 100}},
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate("domain:momentum.ai")

        assert isinstance(result, TractionScore)
        assert result.composite_momentum > 0.0
        assert result.composite_momentum <= 1.0
        assert result.github_stars_growth_30d > 0.0
        assert result.job_posting_velocity > 0.0

    @pytest.mark.asyncio
    async def test_composite_momentum_caps_at_1(self):
        """Should cap composite momentum at 1.0."""
        from utils.traction_calculator import TractionCalculator, TractionScore

        mock_store = AsyncMock()

        # Extreme growth signals
        now = datetime.now(timezone.utc)
        signals = [
            # GitHub: 10 -> 1000 stars (9900% growth)
            {"id": 1, "signal_type": "github_trending", "detected_at": now - timedelta(days=30), "raw_data": {"stars": 10}},
            {"id": 2, "signal_type": "github_trending", "detected_at": now, "raw_data": {"stars": 1000}},
        ]

        mock_store.get_signals_for_traction = AsyncMock(return_value=signals)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate("domain:viral-extreme.io")

        assert result.composite_momentum <= 1.0


class TestMomentumPercentile:
    """Test percentile ranking across all signals."""

    @pytest.mark.asyncio
    async def test_calculate_percentile_rank(self):
        """Should calculate percentile rank vs historical scores."""
        from utils.traction_calculator import TractionCalculator, TractionScore

        mock_store = AsyncMock()

        # Historical scores: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        mock_store.get_historical_traction_scores = AsyncMock(
            return_value=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        )

        # Current signals with strong multi-source momentum
        now = datetime.now(timezone.utc)

        # Side effect to return appropriate signals per type
        def get_signals_side_effect(canonical_key, signal_types):
            all_signals = {
                'github_trending': [
                    {"id": 1, "signal_type": "github_trending", "detected_at": now - timedelta(days=30), "raw_data": {"stars": 100}},
                    {"id": 2, "signal_type": "github_trending", "detected_at": now, "raw_data": {"stars": 300}},  # 200% growth
                ],
                'github_activity': [],
                'hiring_signal': [
                    {"id": 3, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=2)},
                    {"id": 4, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=5)},
                    {"id": 5, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=8)},
                    {"id": 6, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=11)},
                    {"id": 7, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=14)},
                    {"id": 10, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=17)},
                    {"id": 11, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=20)},
                    {"id": 12, "signal_type": "hiring_signal", "detected_at": now - timedelta(days=23)},
                ],
                'product_hunt_launch': [
                    {"id": 8, "signal_type": "product_hunt_launch", "detected_at": now - timedelta(days=30), "raw_data": {"upvotes": 50}},
                    {"id": 9, "signal_type": "product_hunt_launch", "detected_at": now, "raw_data": {"upvotes": 200}},  # 300% growth
                ],
                'hacker_news_mention': [],
            }
            result = []
            for st in signal_types:
                result.extend(all_signals.get(st, []))
            return result

        mock_store.get_signals_for_traction = AsyncMock(side_effect=get_signals_side_effect)

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate("domain:top-performer.ai")

        # With strong multi-source momentum, should be above median
        assert result.momentum_percentile >= 50  # At least median
        assert result.composite_momentum > 0.5  # Strong composite score

    @pytest.mark.asyncio
    async def test_percentile_with_no_history(self):
        """Should default to 50th percentile when no history."""
        from utils.traction_calculator import TractionCalculator

        mock_store = AsyncMock()
        mock_store.get_historical_traction_scores = AsyncMock(return_value=[])
        mock_store.get_signals_for_traction = AsyncMock(return_value=[])

        calculator = TractionCalculator(mock_store)
        result = await calculator.calculate("domain:new-company.com")

        # Default percentile for no data
        assert result.momentum_percentile == 50


class TestTractionScoreDataclass:
    """Test TractionScore dataclass structure."""

    def test_traction_score_has_required_fields(self):
        """TractionScore should have all required momentum fields."""
        from utils.traction_calculator import TractionScore

        score = TractionScore(
            canonical_key="domain:test.com",
            github_stars_growth_30d=0.5,
            github_commit_velocity=10.0,
            job_posting_velocity=2.0,
            job_count_growth_30d=1.5,
            ph_vote_growth_30d=0.8,
            hn_mention_growth_30d=0.3,
            composite_momentum=0.65,
            momentum_percentile=75,
        )

        assert score.canonical_key == "domain:test.com"
        assert score.github_stars_growth_30d == 0.5
        assert score.composite_momentum == 0.65
        assert score.momentum_percentile == 75
