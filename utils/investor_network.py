"""
Investor Network Analyzer - builds co-investment network and calculates centrality.

Part of Deal Intelligence Engine (Phase 3).

This module provides:
- Co-investment graph construction from funding signals
- Eigenvector centrality ranking for investors
- Company investor quality scoring based on centrality
- Network-based deal prioritization (PitchBook-inspired)

Usage:
    analyzer = InvestorNetworkAnalyzer(signal_store)

    # Build network from funding signals
    graph = await analyzer.build_network()

    # Rank investors by centrality
    rankings = await analyzer.rank_investors()

    # Score a company based on its investors
    score = await analyzer.score_company_investors(
        canonical_key="domain:startup.io",
        investors=["Sequoia", "Andreessen Horowitz"]
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional

import networkx as nx

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class InvestorRanking:
    """Ranking of an investor by network centrality."""
    investor_name: str
    centrality_score: float
    coinvestment_count: int
    rank: int


@dataclass
class CompanyInvestorScore:
    """Investor quality score for a company."""
    canonical_key: str
    investor_quality_score: float
    top_investor: Optional[str]
    known_investors_count: int
    total_investors_count: int


# =============================================================================
# INVESTOR NETWORK ANALYZER
# =============================================================================

class InvestorNetworkAnalyzer:
    """
    Analyzes investor co-investment networks.

    Inspired by PitchBook's approach to measuring investor quality
    through network analysis and eigenvector centrality.
    """

    def __init__(self, store):
        """
        Initialize with a signal store.

        Args:
            store: Storage layer with signal access methods
        """
        self.store = store
        self._graph: Optional[nx.Graph] = None
        self._centrality: Dict[str, float] = {}
        self._coinvestment_counts: Dict[str, int] = {}

    async def build_network(self) -> nx.Graph:
        """
        Build co-investment network from funding signals.

        Creates an undirected graph where:
        - Nodes are investors
        - Edges connect investors who co-invested in the same round
        - Edge weights represent number of co-investments

        Returns:
            NetworkX Graph object
        """
        self._graph = nx.Graph()

        # Get funding signals
        try:
            signals = await self.store.get_signals_for_network()
        except (AttributeError, Exception) as e:
            logger.warning(f"Failed to get signals for network: {e}")
            signals = []

        if not signals:
            logger.debug("No funding signals found for network building")
            return self._graph

        # Process each funding round
        for signal in signals:
            raw_data = signal.get('raw_data', {})
            investors = raw_data.get('investors', [])

            if not investors:
                continue

            # Add all investors as nodes
            for investor in investors:
                if investor not in self._graph:
                    self._graph.add_node(investor)
                    self._coinvestment_counts[investor] = 0

            # Add edges for co-investments (combinations of 2)
            if len(investors) >= 2:
                for inv1, inv2 in combinations(investors, 2):
                    if self._graph.has_edge(inv1, inv2):
                        # Increment weight
                        self._graph[inv1][inv2]['weight'] += 1
                    else:
                        # Create edge with weight 1
                        self._graph.add_edge(inv1, inv2, weight=1)

                    # Track co-investment counts
                    self._coinvestment_counts[inv1] += 1
                    self._coinvestment_counts[inv2] += 1

        logger.info(
            f"Built investor network: {self._graph.number_of_nodes()} investors, "
            f"{self._graph.number_of_edges()} co-investment edges"
        )

        return self._graph

    async def rank_investors(self) -> List[InvestorRanking]:
        """
        Rank investors by eigenvector centrality.

        Eigenvector centrality measures how connected an investor is
        to other well-connected investors. Higher scores indicate
        more influential network positions.

        Returns:
            List of InvestorRanking sorted by centrality descending
        """
        if self._graph is None:
            await self.build_network()

        if self._graph.number_of_nodes() == 0:
            return []

        # Calculate eigenvector centrality
        try:
            # For disconnected graphs or small graphs, use power iteration
            if self._graph.number_of_nodes() < 3:
                # Simple degree centrality for very small graphs
                self._centrality = nx.degree_centrality(self._graph)
            else:
                try:
                    self._centrality = nx.eigenvector_centrality(
                        self._graph,
                        max_iter=1000,
                        weight='weight'
                    )
                except nx.PowerIterationFailedConvergence:
                    # Fall back to degree centrality
                    self._centrality = nx.degree_centrality(self._graph)
        except Exception as e:
            logger.warning(f"Centrality calculation failed: {e}")
            # Fall back to simple degree centrality
            self._centrality = nx.degree_centrality(self._graph)

        # Create rankings
        rankings = []
        for investor, score in self._centrality.items():
            rankings.append(InvestorRanking(
                investor_name=investor,
                centrality_score=score,
                coinvestment_count=self._coinvestment_counts.get(investor, 0),
                rank=0,  # Will be set after sorting
            ))

        # Sort by centrality descending
        rankings.sort(key=lambda r: r.centrality_score, reverse=True)

        # Assign ranks
        for i, ranking in enumerate(rankings):
            ranking.rank = i + 1

        logger.debug(f"Ranked {len(rankings)} investors by centrality")
        return rankings

    async def score_company_investors(
        self,
        canonical_key: str,
        investors: List[str],
    ) -> CompanyInvestorScore:
        """
        Score a company based on its investors' network centrality.

        The score is the average centrality of all known investors,
        weighted by their network position.

        Args:
            canonical_key: Company identifier
            investors: List of investor names

        Returns:
            CompanyInvestorScore with quality metrics
        """
        if not self._centrality:
            await self.rank_investors()

        total_investors = len(investors)
        known_investors = 0
        total_centrality = 0.0
        top_investor = None
        top_score = 0.0

        for investor in investors:
            if investor in self._centrality:
                known_investors += 1
                score = self._centrality[investor]
                total_centrality += score

                if score > top_score:
                    top_score = score
                    top_investor = investor

        # Calculate average score for known investors
        if known_investors > 0:
            investor_quality = total_centrality / known_investors
        else:
            investor_quality = 0.0

        return CompanyInvestorScore(
            canonical_key=canonical_key,
            investor_quality_score=investor_quality,
            top_investor=top_investor,
            known_investors_count=known_investors,
            total_investors_count=total_investors,
        )

    def get_investor_centrality(self, investor_name: str) -> float:
        """Get centrality score for a specific investor."""
        return self._centrality.get(investor_name, 0.0)

    def get_coinvestment_count(self, investor_name: str) -> int:
        """Get co-investment count for a specific investor."""
        return self._coinvestment_counts.get(investor_name, 0)
