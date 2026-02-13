"""Integration tests for Web3 detector in production (ThesisFilter) and consumer paths.

Verifies:
- Production: ThesisFilter.classify() rejects crypto signals before keyword scoring
- Production: ThesisFilter.classify() does NOT reject rescued phrases
- Production: Rejection reason string is stable for operators
- Consumer parity: HardDisqualifiers uses Web3Detector
- All existing ThesisFilter routing still works
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.thesis_filter import ThesisFilter, ThesisFilterConfig, RoutingDecision


class TestThesisFilterWeb3Precheck:
    """Web3 pre-check in ThesisFilter.classify() (production path)."""

    @pytest.fixture
    def thesis_filter(self):
        return ThesisFilter(config=ThesisFilterConfig())

    @pytest.mark.asyncio
    async def test_blockchain_startup_rejected(self, thesis_filter):
        """Unambiguous crypto term -> REJECTED before keyword scoring."""
        result = await thesis_filter.classify("blockchain startup for DeFi lending")
        assert result.routing == RoutingDecision.REJECTED
        assert result.rejection_reason is not None
        assert "Unambiguous" in result.rejection_reason
        # matched_term could be any unambiguous term (blockchain, defi, etc.)
        assert len(result.negative_keywords) == 1

    @pytest.mark.asyncio
    async def test_ethereum_token_rejected(self, thesis_filter):
        """Ambiguous term + crypto context -> REJECTED."""
        result = await thesis_filter.classify("token exchange on ethereum network")
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_web3_marketplace_rejected(self, thesis_filter):
        """web3 is unambiguous per thesis exclusion."""
        result = await thesis_filter.classify("web3 marketplace for food delivery")
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_access_token_not_rejected_as_crypto(self, thesis_filter):
        """Rescued phrase 'access token' should NOT trigger Web3 crypto rejection.

        Note: ThesisMatcher still has 'token' as a negative keyword for soft penalty,
        so the signal may still be rejected by the keyword path. The key assertion
        is that the Web3 pre-check did NOT fire (rejection_reason would contain
        'crypto' or 'Unambiguous' if Web3 detector caught it).
        """
        result = await thesis_filter.classify(
            "OAuth access token startup for API authentication",
            skip_llm=True,
        )
        # Web3 pre-check should NOT have fired
        if result.rejection_reason:
            assert "Unambiguous crypto" not in result.rejection_reason
            assert "co-occurs with crypto" not in result.rejection_reason

    @pytest.mark.asyncio
    async def test_dao_pattern_not_rejected(self, thesis_filter):
        """Rescued phrase 'DAO pattern' should NOT trigger crypto rejection."""
        result = await thesis_filter.classify(
            "Software using DAO pattern for database access"
        )
        # Should not be rejected for crypto (may be rejected for other reasons like B2B)
        if result.rejection_reason:
            assert "crypto" not in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_data_mining_not_rejected(self, thesis_filter):
        """Rescued phrase 'data mining' should NOT trigger crypto rejection."""
        result = await thesis_filter.classify(
            "Consumer insights through data mining of purchase behavior"
        )
        if result.rejection_reason:
            assert "crypto" not in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_digital_wallet_not_rejected(self, thesis_filter):
        """Rescued phrase 'digital wallet' should NOT trigger crypto rejection."""
        result = await thesis_filter.classify(
            "Digital wallet for everyday grocery payments"
        )
        if result.rejection_reason:
            assert "crypto" not in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_rejection_reason_stable(self, thesis_filter):
        """Rejection reason should contain the matched term for operator debugging."""
        result = await thesis_filter.classify("bitcoin mining operation")
        assert result.routing == RoutingDecision.REJECTED
        assert "bitcoin" in result.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_consumer_signal_still_qualifies(self, thesis_filter):
        """Non-crypto consumer signal should proceed through normal flow."""
        result = await thesis_filter.classify(
            "Healthy meal kit delivery subscription for busy families",
            skip_llm=True,
        )
        assert result.routing in (RoutingDecision.QUALIFIED, RoutingDecision.HELD)
        assert result.keyword_score > 0  # Should have keyword matches

    @pytest.mark.asyncio
    async def test_keyword_score_zero_when_web3_rejected(self, thesis_filter):
        """When Web3 pre-check rejects, keyword_score should be 0 (never reached)."""
        result = await thesis_filter.classify("ethereum smart contract platform")
        assert result.routing == RoutingDecision.REJECTED
        assert result.keyword_score == 0.0


class TestHardDisqualifiersWeb3Parity:
    """Consumer path: HardDisqualifiers uses Web3Detector."""

    @pytest.fixture
    def disqualifiers(self):
        from consumer.thesis_filter.hard_disqualifiers import HardDisqualifiers
        return HardDisqualifiers()

    def test_blockchain_disqualified(self, disqualifiers):
        result = disqualifiers.check("blockchain startup for payments")
        assert not result.passed
        assert result.category == "crypto"

    def test_access_token_not_disqualified_as_crypto(self, disqualifiers):
        """'access token' should not trigger crypto disqualification."""
        result = disqualifiers.check("API access token management platform")
        # May be disqualified as B2B ("api"), but NOT as crypto
        if not result.passed:
            assert result.category != "crypto"

    def test_dao_pattern_not_disqualified_as_crypto(self, disqualifiers):
        """'DAO pattern' should not trigger crypto disqualification."""
        result = disqualifiers.check("Software using DAO pattern")
        if not result.passed:
            assert result.category != "crypto"

    def test_loyalty_tokens_not_disqualified(self, disqualifiers):
        """'loyalty tokens' rescue phrase should prevent crypto flag."""
        result = disqualifiers.check("Loyalty tokens for customer rewards program")
        if not result.passed:
            assert result.category != "crypto"

    def test_nft_marketplace_disqualified(self, disqualifiers):
        result = disqualifiers.check("NFT marketplace for digital art")
        assert not result.passed
        assert result.category == "crypto"
