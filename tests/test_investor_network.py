"""
Tests for Investor Network Analyzer - builds co-investment network and calculates centrality.

TDD Approach: Tests written before implementation.

Investor network metrics include:
- Co-investment graph construction from funding signals
- Eigenvector centrality ranking for investors
- Company investor quality scoring based on investor centrality
- Network-based deal prioritization (PitchBook-inspired)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from typing import List, Dict, Any


class TestBuildNetwork:
    """Test build_network() - constructs co-investment graph from signals."""

    @pytest.mark.asyncio
    async def test_build_network_from_funding_signals(self):
        """Should build graph with investors as nodes and co-investments as edges."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()

        # Funding signals with investor data
        signals = [
            {
                "id": 1,
                "canonical_key": "domain:startup1.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {
                    "investors": ["Sequoia", "Andreessen Horowitz"],
                    "round_type": "Series A",
                    "amount_usd": 10000000,
                },
            },
            {
                "id": 2,
                "canonical_key": "domain:startup2.io",
                "signal_type": "crunchbase_funding",
                "raw_data": {
                    "investors": ["Sequoia", "Greylock"],
                    "round_type": "Seed",
                    "amount_usd": 5000000,
                },
            },
            {
                "id": 3,
                "canonical_key": "domain:startup3.ai",
                "signal_type": "crunchbase_funding",
                "raw_data": {
                    "investors": ["Andreessen Horowitz", "Greylock"],
                    "round_type": "Series B",
                    "amount_usd": 25000000,
                },
            },
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        graph = await analyzer.build_network()

        # Should have 3 investors as nodes
        assert graph.number_of_nodes() == 3
        assert "Sequoia" in graph.nodes
        assert "Andreessen Horowitz" in graph.nodes
        assert "Greylock" in graph.nodes

        # Should have edges for co-investments
        assert graph.has_edge("Sequoia", "Andreessen Horowitz")  # startup1
        assert graph.has_edge("Sequoia", "Greylock")  # startup2
        assert graph.has_edge("Andreessen Horowitz", "Greylock")  # startup3

    @pytest.mark.asyncio
    async def test_build_network_weights_by_coinvestments(self):
        """Edge weights should increase with more co-investments."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()

        # Multiple co-investments between same investors
        signals = [
            {
                "id": 1,
                "canonical_key": "domain:startup1.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Greylock"]},
            },
            {
                "id": 2,
                "canonical_key": "domain:startup2.io",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Greylock"]},
            },
            {
                "id": 3,
                "canonical_key": "domain:startup3.ai",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Greylock"]},
            },
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        graph = await analyzer.build_network()

        # Edge weight should be 3 (3 co-investments)
        assert graph[("Sequoia")]["Greylock"]["weight"] == 3

    @pytest.mark.asyncio
    async def test_build_network_empty_signals(self):
        """Should return empty graph when no funding signals exist."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()
        mock_store.get_signals_for_network = AsyncMock(return_value=[])

        analyzer = InvestorNetworkAnalyzer(mock_store)
        graph = await analyzer.build_network()

        assert graph.number_of_nodes() == 0
        assert graph.number_of_edges() == 0

    @pytest.mark.asyncio
    async def test_build_network_single_investor_rounds(self):
        """Should handle rounds with single investor (no co-investment edges)."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()

        signals = [
            {
                "id": 1,
                "canonical_key": "domain:solo.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Solo Capital"]},
            },
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        graph = await analyzer.build_network()

        # Should have the investor as a node but no edges
        assert graph.number_of_nodes() == 1
        assert "Solo Capital" in graph.nodes
        assert graph.number_of_edges() == 0


class TestRankInvestors:
    """Test rank_investors() - calculates eigenvector centrality."""

    @pytest.mark.asyncio
    async def test_rank_investors_by_centrality(self):
        """Should rank investors by eigenvector centrality."""
        from utils.investor_network import InvestorNetworkAnalyzer, InvestorRanking

        mock_store = AsyncMock()

        # Create a network where one investor is more central
        # Sequoia invests with everyone, others only invest with Sequoia
        signals = [
            {
                "id": 1,
                "canonical_key": "domain:a.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Investor A"]},
            },
            {
                "id": 2,
                "canonical_key": "domain:b.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Investor B"]},
            },
            {
                "id": 3,
                "canonical_key": "domain:c.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Investor C"]},
            },
            {
                "id": 4,
                "canonical_key": "domain:d.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["Sequoia", "Investor D"]},
            },
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        rankings = await analyzer.rank_investors()

        # Sequoia should be ranked first (highest centrality)
        assert len(rankings) > 0
        assert rankings[0].investor_name == "Sequoia"
        assert rankings[0].centrality_score > 0

    @pytest.mark.asyncio
    async def test_rank_investors_returns_sorted_list(self):
        """Rankings should be sorted by centrality score descending."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()

        signals = [
            {
                "id": 1,
                "canonical_key": "domain:a.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["A", "B", "C"]},
            },
            {
                "id": 2,
                "canonical_key": "domain:b.com",
                "signal_type": "crunchbase_funding",
                "raw_data": {"investors": ["A", "B"]},
            },
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        rankings = await analyzer.rank_investors()

        # Verify sorted descending
        for i in range(len(rankings) - 1):
            assert rankings[i].centrality_score >= rankings[i + 1].centrality_score

    @pytest.mark.asyncio
    async def test_rank_investors_empty_network(self):
        """Should return empty list for empty network."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()
        mock_store.get_signals_for_network = AsyncMock(return_value=[])

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        rankings = await analyzer.rank_investors()

        assert rankings == []


class TestScoreCompanyInvestors:
    """Test score_company_investors() - scores company based on investor quality."""

    @pytest.mark.asyncio
    async def test_score_company_with_top_investors(self):
        """Company with top-tier investors should get high score."""
        from utils.investor_network import InvestorNetworkAnalyzer, CompanyInvestorScore

        mock_store = AsyncMock()

        # Build a network first
        network_signals = [
            {"id": 1, "canonical_key": "domain:a.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Sequoia", "Investor A"]}},
            {"id": 2, "canonical_key": "domain:b.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Sequoia", "Investor B"]}},
            {"id": 3, "canonical_key": "domain:c.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Sequoia", "Investor C"]}},
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=network_signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        await analyzer.rank_investors()

        # Score a company backed by Sequoia (top investor)
        score = await analyzer.score_company_investors(
            canonical_key="domain:target.com",
            investors=["Sequoia"]
        )

        assert isinstance(score, CompanyInvestorScore)
        assert score.investor_quality_score > 0.5  # High score for top investor
        assert score.top_investor == "Sequoia"

    @pytest.mark.asyncio
    async def test_score_company_with_unknown_investors(self):
        """Company with unknown investors should get lower score."""
        from utils.investor_network import InvestorNetworkAnalyzer, CompanyInvestorScore

        mock_store = AsyncMock()

        network_signals = [
            {"id": 1, "canonical_key": "domain:a.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Known Investor", "Another Known"]}},
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=network_signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        await analyzer.rank_investors()

        # Score a company backed by unknown investor
        score = await analyzer.score_company_investors(
            canonical_key="domain:unknown-backed.com",
            investors=["Unknown Angel"]
        )

        assert score.investor_quality_score == 0.0  # Unknown investor = 0 score
        assert score.known_investors_count == 0

    @pytest.mark.asyncio
    async def test_score_company_averages_multiple_investors(self):
        """Score should be average of all investors' centrality."""
        from utils.investor_network import InvestorNetworkAnalyzer

        mock_store = AsyncMock()

        network_signals = [
            {"id": 1, "canonical_key": "domain:a.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Top Tier", "Mid Tier", "Low Tier"]}},
            {"id": 2, "canonical_key": "domain:b.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Top Tier", "Mid Tier"]}},
            {"id": 3, "canonical_key": "domain:c.com", "signal_type": "crunchbase_funding",
             "raw_data": {"investors": ["Top Tier"]}},
        ]

        mock_store.get_signals_for_network = AsyncMock(return_value=network_signals)

        analyzer = InvestorNetworkAnalyzer(mock_store)
        await analyzer.build_network()
        await analyzer.rank_investors()

        # Score company with multiple investors
        score = await analyzer.score_company_investors(
            canonical_key="domain:multi.com",
            investors=["Top Tier", "Low Tier"]
        )

        assert score.known_investors_count == 2
        # Score should be between top and low tier
        assert 0 < score.investor_quality_score < 1.0


class TestInvestorRankingDataclass:
    """Test InvestorRanking dataclass structure."""

    def test_investor_ranking_has_required_fields(self):
        """InvestorRanking should have all required fields."""
        from utils.investor_network import InvestorRanking

        ranking = InvestorRanking(
            investor_name="Sequoia Capital",
            centrality_score=0.85,
            coinvestment_count=150,
            rank=1,
        )

        assert ranking.investor_name == "Sequoia Capital"
        assert ranking.centrality_score == 0.85
        assert ranking.coinvestment_count == 150
        assert ranking.rank == 1


class TestCompanyInvestorScoreDataclass:
    """Test CompanyInvestorScore dataclass structure."""

    def test_company_investor_score_has_required_fields(self):
        """CompanyInvestorScore should have all required fields."""
        from utils.investor_network import CompanyInvestorScore

        score = CompanyInvestorScore(
            canonical_key="domain:startup.io",
            investor_quality_score=0.75,
            top_investor="Sequoia",
            known_investors_count=2,
            total_investors_count=3,
        )

        assert score.canonical_key == "domain:startup.io"
        assert score.investor_quality_score == 0.75
        assert score.top_investor == "Sequoia"
        assert score.known_investors_count == 2
        assert score.total_investors_count == 3
