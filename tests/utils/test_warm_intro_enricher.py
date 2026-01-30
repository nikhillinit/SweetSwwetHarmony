"""
Tests for WarmIntroEnricher service.

Phase A1: Warm intro enrichment for investor matches.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest

from utils.warm_intro_enricher import WarmIntroEnricher
from utils.warm_intro_boost import WarmIntroBoost, WarmIntroCandidate, RelationshipSource
from storage.relationship_store import CombinedRelationship


class MockRelationshipStore:
    """Mock RelationshipStore for testing."""

    def __init__(self, relationships=None):
        self.relationships = relationships or {}

    async def initialize(self):
        pass

    async def get_combined_relationship(
        self, me_email: str, target_domain: str
    ) -> "CombinedRelationship | None":
        return self.relationships.get((me_email, target_domain))


@pytest.fixture
def warm_intro_boost():
    """Create WarmIntroBoost instance."""
    return WarmIntroBoost()


@pytest.fixture
def mock_relationship_store():
    """Create mock RelationshipStore."""
    return MockRelationshipStore()


class TestWarmIntroEnricherBasic:
    """Basic tests for WarmIntroEnricher."""

    def test_enricher_initialization(self, mock_relationship_store, warm_intro_boost):
        """A1.1: WarmIntroEnricher initializes with store and boost."""
        enricher = WarmIntroEnricher(
            relationship_store=mock_relationship_store,
            warm_intro_boost=warm_intro_boost,
        )

        assert enricher.store is mock_relationship_store
        assert enricher.boost is warm_intro_boost

    @pytest.mark.asyncio
    async def test_enrich_investor_no_relationship(
        self, mock_relationship_store, warm_intro_boost
    ):
        """A1.1: Returns None when no relationship exists."""
        enricher = WarmIntroEnricher(
            relationship_store=mock_relationship_store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="sequoia.com",
            user_email="user@example.com",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_investor_with_gmail_relationship(
        self, warm_intro_boost
    ):
        """A1.1: Returns WarmIntroCandidate for Gmail relationship."""
        store = MockRelationshipStore({
            ("user@example.com", "sequoia.com"): CombinedRelationship(
                target_domain="sequoia.com",
                gmail_score=0.75,
                notion_score=None,
                lp_status=None,
                lp_name=None,
                intro_count=3,
                reply_count=2,
                total_messages=10,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="sequoia.com",
            user_email="user@example.com",
        )

        assert result is not None
        assert result.investor_domain == "sequoia.com"
        assert result.score == 0.75
        assert result.source == RelationshipSource.GMAIL

    @pytest.mark.asyncio
    async def test_enrich_investor_with_notion_relationship(
        self, warm_intro_boost
    ):
        """A1.1: Returns WarmIntroCandidate for Notion LP relationship."""
        store = MockRelationshipStore({
            ("user@example.com", "benchmark.com"): CombinedRelationship(
                target_domain="benchmark.com",
                gmail_score=None,
                notion_score=0.95,
                lp_status="Docs Signed",
                lp_name="Benchmark Capital",
                intro_count=0,
                reply_count=0,
                total_messages=0,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="benchmark.com",
            user_email="user@example.com",
        )

        assert result is not None
        assert result.investor_domain == "benchmark.com"
        assert result.score == 0.95
        assert result.source == RelationshipSource.NOTION_LP


class TestWarmIntroEnricherScoring:
    """Tests for warm intro scoring logic."""

    @pytest.mark.asyncio
    async def test_merge_uses_max_score(self, warm_intro_boost):
        """A1.1: Merged score is max(gmail, notion)."""
        store = MockRelationshipStore({
            ("user@example.com", "a16z.com"): CombinedRelationship(
                target_domain="a16z.com",
                gmail_score=0.60,
                notion_score=0.85,
                lp_status="Active",
                lp_name="a16z",
                intro_count=2,
                reply_count=1,
                total_messages=5,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="a16z.com",
            user_email="user@example.com",
        )

        assert result is not None
        # Max of 0.60 and 0.85 = 0.85
        assert result.score == 0.85
        # Source should be NOTION_LP since it won
        assert result.source == RelationshipSource.NOTION_LP


class TestWarmIntroEnricherDeclined:
    """Tests for declined LP suppression."""

    @pytest.mark.asyncio
    async def test_declined_lp_suppressed(self, warm_intro_boost):
        """A1.1: Declined LP within window is suppressed (returns None)."""
        # The current implementation doesn't have declined_at in CombinedRelationship
        # So we test with lp_status="Declined" which should be suppressed
        store = MockRelationshipStore({
            ("user@example.com", "greylock.com"): CombinedRelationship(
                target_domain="greylock.com",
                gmail_score=0.50,
                notion_score=0.70,
                lp_status="Declined",
                lp_name="Greylock",
                intro_count=1,
                reply_count=0,
                total_messages=3,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="greylock.com",
            user_email="user@example.com",
        )

        # Without declined_at, safe default is to suppress
        assert result is None


class TestWarmIntroEnricherBatch:
    """Tests for batch enrichment."""

    @pytest.mark.asyncio
    async def test_enrich_multiple_investors(self, warm_intro_boost):
        """A1.2: Batch enrichment returns dict of domain -> candidate."""
        store = MockRelationshipStore({
            ("user@example.com", "sequoia.com"): CombinedRelationship(
                target_domain="sequoia.com",
                gmail_score=0.80,
                notion_score=None,
                lp_status=None,
                lp_name=None,
                intro_count=5,
                reply_count=3,
                total_messages=15,
            ),
            ("user@example.com", "a16z.com"): CombinedRelationship(
                target_domain="a16z.com",
                gmail_score=None,
                notion_score=0.90,
                lp_status="Active",
                lp_name="a16z",
                intro_count=0,
                reply_count=0,
                total_messages=0,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        # Create mock investor matches with website_domain
        class MockInvestorMatch:
            def __init__(self, investor_domain):
                self.investor_domain = investor_domain

        investor_matches = [
            MockInvestorMatch("sequoia.com"),
            MockInvestorMatch("a16z.com"),
            MockInvestorMatch("unknown.com"),  # No relationship
        ]

        results = await enricher.enrich_investor_matches(
            investor_matches=investor_matches,
            user_email="user@example.com",
        )

        # Should have 2 results (unknown.com has no relationship)
        assert len(results) == 2
        assert "sequoia.com" in results
        assert "a16z.com" in results
        assert "unknown.com" not in results

        assert results["sequoia.com"].score == 0.80
        assert results["a16z.com"].score == 0.90

    @pytest.mark.asyncio
    async def test_enrich_empty_matches(self, mock_relationship_store, warm_intro_boost):
        """A1.2: Empty investor matches returns empty dict."""
        enricher = WarmIntroEnricher(
            relationship_store=mock_relationship_store,
            warm_intro_boost=warm_intro_boost,
        )

        results = await enricher.enrich_investor_matches(
            investor_matches=[],
            user_email="user@example.com",
        )

        assert results == {}


class TestWarmIntroEnricherEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_missing_gmail_score(self, warm_intro_boost):
        """A1.4: Handles missing gmail_score gracefully."""
        store = MockRelationshipStore({
            ("user@example.com", "test.com"): CombinedRelationship(
                target_domain="test.com",
                gmail_score=None,
                notion_score=0.65,
                lp_status="Active",
                lp_name="Test",
                intro_count=0,
                reply_count=0,
                total_messages=0,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="test.com",
            user_email="user@example.com",
        )

        assert result is not None
        assert result.score == 0.65

    @pytest.mark.asyncio
    async def test_missing_notion_score(self, warm_intro_boost):
        """A1.4: Handles missing notion_score gracefully."""
        store = MockRelationshipStore({
            ("user@example.com", "test2.com"): CombinedRelationship(
                target_domain="test2.com",
                gmail_score=0.55,
                notion_score=None,
                lp_status=None,
                lp_name=None,
                intro_count=2,
                reply_count=1,
                total_messages=5,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="test2.com",
            user_email="user@example.com",
        )

        assert result is not None
        assert result.score == 0.55

    @pytest.mark.asyncio
    async def test_both_scores_none_returns_none(self, warm_intro_boost):
        """A1.4: Both scores None means no relationship (returns None)."""
        store = MockRelationshipStore({
            ("user@example.com", "empty.com"): CombinedRelationship(
                target_domain="empty.com",
                gmail_score=None,
                notion_score=None,
                lp_status=None,
                lp_name=None,
                intro_count=0,
                reply_count=0,
                total_messages=0,
            ),
        })

        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=warm_intro_boost,
        )

        result = await enricher.enrich_investor(
            investor_domain="empty.com",
            user_email="user@example.com",
        )

        # No meaningful relationship data
        assert result is None
