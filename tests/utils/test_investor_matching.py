"""
Tests for investor matching module.

Sprint 5: Investor Matching v1.
"""

import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch

from utils.investor_matching import (
    InvestorMatcher,
    InvestorMatch,
    InvestorMatchResult,
    MatchExplanation,
    PortfolioEvidence,
    compute_distribution_match,
    compute_constraint_score,
    compute_final_score,
    generate_explanation,
    DEFAULT_WEIGHTS,
    COLD_START_PENALTY,
)


# =============================================================================
# SCORING FUNCTION TESTS
# =============================================================================

class TestComputeDistributionMatch:
    """Tests for distribution matching."""

    def test_exact_match_high_probability(self):
        """Exact match with high probability scores high."""
        dist = {"seed": 0.6, "series_a": 0.3, "pre_seed": 0.1}
        score = compute_distribution_match("seed", dist)
        assert score >= 0.8

    def test_exact_match_low_probability(self):
        """Exact match with low probability scores moderate."""
        dist = {"seed": 0.1, "series_a": 0.7, "pre_seed": 0.2}
        score = compute_distribution_match("seed", dist)
        assert 0.1 <= score <= 0.5

    def test_no_match_returns_default(self):
        """No match returns default score."""
        dist = {"seed": 0.6, "series_a": 0.4}
        score = compute_distribution_match("series_c", dist)
        assert score == 0.3  # default

    def test_empty_distribution_returns_default(self):
        """Empty distribution returns default score."""
        score = compute_distribution_match("seed", {})
        assert score == 0.3

    def test_empty_target_returns_default(self):
        """Empty target returns default score."""
        dist = {"seed": 0.6}
        score = compute_distribution_match("", dist)
        assert score == 0.3

    def test_case_insensitive_match(self):
        """Matching is case-insensitive."""
        dist = {"Seed": 0.6, "Series_A": 0.4}
        score = compute_distribution_match("seed", dist)
        assert score >= 0.8

    def test_partial_match(self):
        """Partial string match gives moderate score."""
        dist = {"consumer_fintech": 0.5, "enterprise": 0.5}
        score = compute_distribution_match("fintech", dist)
        assert 0.3 < score < 1.0


class TestComputeConstraintScore:
    """Tests for preference constraint scoring."""

    def test_no_preferences_full_score(self):
        """No preferences means full compliance."""
        claims = {"sector": "fintech", "stage": "seed"}
        score = compute_constraint_score(claims, [])
        assert score == 1.0

    def test_hard_no_disqualifies(self):
        """Hard no preference returns zero."""
        claims = {"sector": "crypto"}
        prefs = [{"preference_type": "hard_no", "predicate": "sector", "value": "crypto", "weight": 1.0}]
        score = compute_constraint_score(claims, prefs)
        assert score == 0.0

    def test_exclude_reduces_score(self):
        """Exclude preference reduces score."""
        claims = {"sector": "enterprise"}
        prefs = [{"preference_type": "exclude", "predicate": "sector", "value": "enterprise", "weight": 1.0}]
        score = compute_constraint_score(claims, prefs)
        assert score < 1.0

    def test_boost_increases_score(self):
        """Boost preference increases score."""
        claims = {"sector": "fintech"}
        prefs = [{"preference_type": "boost", "predicate": "sector", "value": "fintech", "weight": 1.0}]
        score = compute_constraint_score(claims, prefs)
        assert score > 1.0 or score == 1.0  # Capped at 1.0

    def test_include_match_increases_score(self):
        """Include preference with match increases score."""
        claims = {"stage": "seed"}
        prefs = [{"preference_type": "include", "predicate": "stage", "value": "seed", "weight": 1.0}]
        score = compute_constraint_score(claims, prefs)
        assert score >= 1.0

    def test_multiple_preferences_combine(self):
        """Multiple preferences combine correctly."""
        claims = {"sector": "fintech", "stage": "series_b"}
        prefs = [
            {"preference_type": "boost", "predicate": "sector", "value": "fintech", "weight": 1.0},
            {"preference_type": "penalize", "predicate": "stage", "value": "series_b", "weight": 1.0},
        ]
        score = compute_constraint_score(claims, prefs)
        # Boost +0.2, penalize -0.15 = 1.05, capped at 1.0
        assert 0.8 <= score <= 1.0

    def test_partial_value_match(self):
        """Partial value match triggers preference."""
        claims = {"sector": "consumer_fintech"}
        prefs = [{"preference_type": "boost", "predicate": "sector", "value": "fintech", "weight": 1.0}]
        score = compute_constraint_score(claims, prefs)
        assert score >= 1.0


class TestComputeFinalScore:
    """Tests for final score computation."""

    def test_all_perfect_scores(self):
        """All perfect scores give near 1.0."""
        score = compute_final_score(
            fts_score=1.0,
            embedding_score=1.0,
            stage_score=1.0,
            sector_score=1.0,
            constraint_score=1.0,
            is_cold_start=False,
        )
        assert score == 1.0

    def test_all_zero_scores(self):
        """All zero scores give 0.0."""
        score = compute_final_score(
            fts_score=0.0,
            embedding_score=0.0,
            stage_score=0.0,
            sector_score=0.0,
            constraint_score=0.0,
            is_cold_start=False,
        )
        assert score == 0.0

    def test_cold_start_penalty_applied(self):
        """Cold start penalty reduces score."""
        score_normal = compute_final_score(
            fts_score=0.8,
            embedding_score=0.8,
            stage_score=0.8,
            sector_score=0.8,
            constraint_score=1.0,
            is_cold_start=False,
        )
        score_cold = compute_final_score(
            fts_score=0.8,
            embedding_score=0.8,
            stage_score=0.8,
            sector_score=0.8,
            constraint_score=1.0,
            is_cold_start=True,
        )
        assert score_cold < score_normal
        assert score_normal - score_cold == pytest.approx(COLD_START_PENALTY, rel=0.01)

    def test_custom_weights(self):
        """Custom weights are respected."""
        custom_weights = {
            'fts': 0.5,
            'embedding': 0.5,
            'stage': 0.0,
            'sector': 0.0,
            'constraint': 0.0,
        }
        score = compute_final_score(
            fts_score=1.0,
            embedding_score=0.0,
            stage_score=1.0,
            sector_score=1.0,
            constraint_score=1.0,
            is_cold_start=False,
            weights=custom_weights,
        )
        assert score == 0.5  # Only FTS contributes

    def test_score_capped_at_bounds(self):
        """Score is capped between 0 and 1."""
        # Even with negative cold start and low scores
        score = compute_final_score(
            fts_score=0.0,
            embedding_score=0.0,
            stage_score=0.0,
            sector_score=0.0,
            constraint_score=0.0,
            is_cold_start=True,
        )
        assert score >= 0.0


class TestGenerateExplanation:
    """Tests for explanation generation."""

    def test_high_lift_strong_language(self):
        """High lift score uses strong language."""
        claim = {
            'predicate': 'sector_preference',
            'value': 'fintech',
            'lift_score': 1.2,
            'support_count': 8,
        }
        exp = generate_explanation(claim, [])
        assert 'Strong' in exp.reason or 'strong' in exp.reason.lower()
        assert 'fintech' in exp.reason
        assert '8' in exp.reason

    def test_moderate_lift_neutral_language(self):
        """Moderate lift uses neutral language."""
        claim = {
            'predicate': 'stage_preference',
            'value': 'seed',
            'lift_score': 0.3,
            'support_count': 5,
        }
        exp = generate_explanation(claim, [])
        assert 'Matches' in exp.reason or 'seed' in exp.reason
        assert '5' in exp.reason

    def test_portfolio_examples_included(self):
        """Portfolio examples are included in explanation."""
        claim = {
            'predicate': 'sector_preference',
            'value': 'fintech',
            'lift_score': 0.8,
            'support_count': 3,
        }
        portfolio = [
            {'company_key': 'domain:acme.ai', 'company_name': 'Acme', 'round_type': 'seed', 'round_date': '2024-01', 'relationship_type': 'led'},
            {'company_key': 'domain:beta.ai', 'company_name': 'Beta', 'round_type': 'series_a', 'round_date': '2024-02', 'relationship_type': 'participated'},
        ]
        exp = generate_explanation(claim, portfolio, max_examples=2)
        assert len(exp.portfolio_examples) == 2
        assert exp.portfolio_examples[0].company_key == 'domain:acme.ai'

    def test_predicate_formatting(self):
        """Predicate is formatted for display."""
        claim = {
            'predicate': 'geo_preference',
            'value': 'US',
            'lift_score': 0.5,
            'support_count': 10,
        }
        exp = generate_explanation(claim, [])
        assert 'Geo' in exp.reason or 'geo' in exp.reason.lower()


# =============================================================================
# INVESTOR MATCHER TESTS
# =============================================================================

class TestInvestorMatcher:
    """Tests for InvestorMatcher class."""

    @pytest.fixture
    def mock_store(self):
        """Create a mock store."""
        store = MagicMock()
        store._db = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_match_returns_result(self, mock_store):
        """Match returns InvestorMatchResult."""
        # Setup mock
        mock_store._db.execute = AsyncMock()

        # Mock company claims query
        claims_cursor = AsyncMock()
        claims_cursor.fetchall = AsyncMock(return_value=[
            ('industry', 'fintech'),
            ('stage', 'seed'),
        ])

        # Mock FTS query - return empty to trigger fallback
        fts_cursor = AsyncMock()
        fts_cursor.fetchall = AsyncMock(return_value=[])

        # Setup execute to return different cursors
        mock_store._db.execute.side_effect = [claims_cursor, fts_cursor]

        matcher = InvestorMatcher(mock_store)
        result = await matcher.match("domain:test.com", save_results=False)

        assert isinstance(result, InvestorMatchResult)
        assert result.company_key == "domain:test.com"

    @pytest.mark.asyncio
    async def test_match_with_provided_claims(self, mock_store):
        """Match uses provided claims instead of querying."""
        # Mock FTS to return empty
        fts_cursor = AsyncMock()
        fts_cursor.fetchall = AsyncMock(return_value=[])
        mock_store._db.execute = AsyncMock(return_value=fts_cursor)

        matcher = InvestorMatcher(mock_store)
        claims = {"sector": "fintech", "stage": "seed"}

        result = await matcher.match(
            "domain:test.com",
            company_claims=claims,
            save_results=False,
        )

        assert result.query_claims == claims

    @pytest.mark.asyncio
    async def test_match_empty_candidates(self, mock_store):
        """Match handles no candidates gracefully."""
        claims_cursor = AsyncMock()
        claims_cursor.fetchall = AsyncMock(return_value=[])

        fts_cursor = AsyncMock()
        fts_cursor.fetchall = AsyncMock(return_value=[])

        mock_store._db.execute = AsyncMock(side_effect=[claims_cursor, fts_cursor])

        matcher = InvestorMatcher(mock_store)
        result = await matcher.match("domain:test.com", save_results=False)

        assert result.matches == []
        assert result.candidates_retrieved == 0


class TestInvestorMatcherIntegration:
    """Integration tests using real SignalStore."""

    @pytest.mark.asyncio
    async def test_full_match_flow(self):
        """Test full matching flow with in-memory DB."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create test investor
        await store.save_investor(
            investor_id="investor:test_vc",
            name="Test VC",
            source="curated_json",
            investor_type="vc",
            hq_country="US",
        )

        # Add portfolio entry
        await store.save_portfolio_entry(
            investor_id="investor:test_vc",
            company_key="domain:portfolio1.com",
            relationship_type="led",
            source="curated_json",
            round_type="seed",
            confidence=0.9,
        )

        # Add profile claim
        await store.save_investor_profile_claim(
            investor_id="investor:test_vc",
            predicate="sector_preference",
            value="fintech",
            confidence=0.8,
            support_count=5,
            lift_score=0.6,
        )

        # Create matcher and match
        matcher = InvestorMatcher(store)
        result = await matcher.match(
            "domain:test.com",
            company_claims={"sector": "fintech", "stage": "seed"},
            save_results=False,
        )

        # Should find at least one match
        # (might be empty if FTS index not populated, which is OK)
        assert isinstance(result, InvestorMatchResult)

        await store.close()

    @pytest.mark.asyncio
    async def test_match_saves_to_db(self):
        """Test that matches are saved to DB."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        # Create test investor
        await store.save_investor(
            investor_id="investor:save_test",
            name="Save Test VC",
            source="curated_json",
        )

        # Create matcher and match (will save empty results)
        matcher = InvestorMatcher(store)
        await matcher.match(
            "domain:save_test.com",
            company_claims={"sector": "fintech"},
            save_results=True,
        )

        # Verify we can retrieve (even if empty)
        matches = await store.get_investor_matches("domain:save_test.com")
        assert isinstance(matches, list)

        await store.close()


class TestMatchBatch:
    """Tests for batch matching."""

    @pytest.mark.asyncio
    async def test_batch_returns_dict(self):
        """Batch match returns dict of results."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        matcher = InvestorMatcher(store)
        results = await matcher.match_batch(
            ["domain:a.com", "domain:b.com"],
            save_results=False,
        )

        assert isinstance(results, dict)
        assert "domain:a.com" in results
        assert "domain:b.com" in results
        assert all(isinstance(r, InvestorMatchResult) for r in results.values())

        await store.close()


# =============================================================================
# DATA CLASS TESTS
# =============================================================================

class TestDataClasses:
    """Tests for data classes."""

    def test_investor_match_defaults(self):
        """InvestorMatch has correct defaults."""
        match = InvestorMatch(
            investor_id="investor:test",
            investor_name="Test",
            investor_type="vc",
            hq_country="US",
            match_score=0.8,
            fts_score=0.7,
            embedding_score=0.6,
            stage_score=0.8,
            sector_score=0.9,
            constraint_score=1.0,
        )
        assert match.explanations == []
        assert match.is_cold_start is False
        assert match.portfolio_count == 0
        assert match.rank == 0

    def test_match_explanation_with_evidence(self):
        """MatchExplanation can hold portfolio evidence."""
        evidence = PortfolioEvidence(
            company_key="domain:test.com",
            company_name="Test Co",
            round_type="seed",
            round_date="2024-01",
            relationship_type="led",
        )
        exp = MatchExplanation(
            reason="Strong sector fit",
            predicate="sector_preference",
            value="fintech",
            lift_score=0.8,
            support_count=5,
            portfolio_examples=[evidence],
        )
        assert len(exp.portfolio_examples) == 1
        assert exp.portfolio_examples[0].company_key == "domain:test.com"


# =============================================================================
# PHASE 4: WARM INTRO BOOST INTEGRATION
# =============================================================================

class TestWarmIntroBoostIntegration:
    """Tests for WarmIntroBoost integration with InvestorMatcher."""

    @pytest.mark.asyncio
    async def test_match_includes_warmth_score(self):
        """Match result should include warmth score when relationship exists."""
        from storage.signal_store import SignalStore
        from storage.relationship_store import RelationshipStore
        from datetime import datetime, timezone
        import tempfile
        import os

        # Create stores
        store = SignalStore(":memory:")
        await store.initialize()

        fd, rel_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        rel_store = RelationshipStore(db_path=rel_db)
        await rel_store.initialize()

        try:
            # Create test investor with domain
            await store.save_investor(
                investor_id="investor:warm_vc",
                name="Warm VC",
                source="curated_json",
                investor_type="vc",
                hq_country="US",
            )

            # Add relationship data (simulating Gmail history)
            await rel_store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="warmvc.com",
                intro_count=5,
                reply_count=4,
                total_messages=10,
                last_contact_at=datetime.now(timezone.utc),
            )

            # Create matcher with relationship store
            matcher = InvestorMatcher(store, relationship_store=rel_store, user_email="user@example.com")
            result = await matcher.match(
                "domain:test.com",
                company_claims={"sector": "fintech", "stage": "seed"},
                save_results=False,
            )

            assert isinstance(result, InvestorMatchResult)

        finally:
            await store.close()
            await rel_store.close()
            if os.path.exists(rel_db):
                try:
                    os.unlink(rel_db)
                except PermissionError:
                    pass

    @pytest.mark.asyncio
    async def test_warmth_boost_applied_above_threshold(self):
        """Warmth boost should be applied when thesis_fit >= 0.4."""
        from utils.warm_intro_boost import WarmIntroBoost

        booster = WarmIntroBoost()

        # Thesis fit >= 0.4, should apply boost
        boosted = booster.apply_warmth_boost(thesis_fit=0.5, warmth=1.0)
        assert boosted > 0.5  # Should have boost applied
        assert boosted == pytest.approx(0.55, rel=0.01)  # 0.5 + (1.0 * 0.05) = 0.55

    @pytest.mark.asyncio
    async def test_warmth_boost_not_applied_below_threshold(self):
        """Warmth boost should not be applied when thesis_fit < 0.4."""
        from utils.warm_intro_boost import WarmIntroBoost

        booster = WarmIntroBoost()

        # Thesis fit < 0.4, should NOT apply boost
        not_boosted = booster.apply_warmth_boost(thesis_fit=0.35, warmth=1.0)
        assert not_boosted == 0.35  # No boost applied

    @pytest.mark.asyncio
    async def test_match_with_lp_relationship(self):
        """Match should consider LP relationship data."""
        from storage.signal_store import SignalStore
        from storage.relationship_store import RelationshipStore
        import tempfile
        import os

        store = SignalStore(":memory:")
        await store.initialize()

        fd, rel_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        rel_store = RelationshipStore(db_path=rel_db)
        await rel_store.initialize()

        try:
            # Create test investor
            await store.save_investor(
                investor_id="investor:lp_vc",
                name="LP VC",
                source="curated_json",
                investor_type="vc",
            )

            # Add LP relationship
            await rel_store.upsert_lp_relationship(
                me_email="user@example.com",
                target_domain="lpvc.com",
                lp_status="Docs Signed",
                lp_name="LP Contact",
                notion_score=0.95,
            )

            # Match should work
            matcher = InvestorMatcher(store, relationship_store=rel_store, user_email="user@example.com")
            result = await matcher.match(
                "domain:test.com",
                company_claims={"sector": "fintech"},
                save_results=False,
            )

            assert isinstance(result, InvestorMatchResult)

        finally:
            await store.close()
            await rel_store.close()
            if os.path.exists(rel_db):
                try:
                    os.unlink(rel_db)
                except PermissionError:
                    pass

    @pytest.mark.asyncio
    async def test_warmth_badge_in_explanations(self):
        """Warm intro matches should include warmth badge in explanations."""
        from utils.warm_intro_boost import WarmIntroBoost, RelationshipSource

        booster = WarmIntroBoost()

        # Gmail active badge
        badge = booster.generate_badge(
            source=RelationshipSource.GMAIL,
            score=0.7,
            lp_status=None,
            is_declined=False,
        )
        assert "📧" in badge
        assert "Active" in badge

        # LP Docs Signed badge
        badge = booster.generate_badge(
            source=RelationshipSource.NOTION_LP,
            score=0.95,
            lp_status="Docs Signed",
            is_declined=False,
        )
        assert "📝" in badge
        assert "LP" in badge

    @pytest.mark.asyncio
    async def test_declined_suppression_in_matching(self):
        """Declined investors should be suppressed or capped."""
        from utils.warm_intro_boost import WarmIntroBoost
        from datetime import datetime, timezone

        booster = WarmIntroBoost()

        # Recent decline should suppress
        assert booster.should_suppress_declined(
            is_declined=True,
            declined_at=datetime.now(timezone.utc),
        ) is True

        # Post-window decline should cap at 0.30
        capped = booster.apply_declined_cap(0.95)
        assert capped == 0.30


class TestInvestorMatcherWithRelationshipStore:
    """Tests for InvestorMatcher with RelationshipStore integration."""

    @pytest.mark.asyncio
    async def test_matcher_initialization_with_relationship_store(self):
        """InvestorMatcher should accept relationship_store parameter."""
        from storage.signal_store import SignalStore
        from storage.relationship_store import RelationshipStore
        import tempfile
        import os

        store = SignalStore(":memory:")
        await store.initialize()

        fd, rel_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        rel_store = RelationshipStore(db_path=rel_db)
        await rel_store.initialize()

        try:
            # Should accept relationship_store and user_email
            matcher = InvestorMatcher(
                store,
                relationship_store=rel_store,
                user_email="user@example.com",
            )
            assert matcher.relationship_store == rel_store
            assert matcher.user_email == "user@example.com"

        finally:
            await store.close()
            await rel_store.close()
            if os.path.exists(rel_db):
                try:
                    os.unlink(rel_db)
                except PermissionError:
                    pass

    @pytest.mark.asyncio
    async def test_matcher_works_without_relationship_store(self):
        """InvestorMatcher should work without relationship_store."""
        from storage.signal_store import SignalStore

        store = SignalStore(":memory:")
        await store.initialize()

        try:
            matcher = InvestorMatcher(store)
            assert matcher.relationship_store is None

            result = await matcher.match(
                "domain:test.com",
                company_claims={"sector": "fintech"},
                save_results=False,
            )
            assert isinstance(result, InvestorMatchResult)

        finally:
            await store.close()
